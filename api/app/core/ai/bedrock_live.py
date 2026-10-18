"""The live model client: AWS Bedrock Converse in the cell's region (rule 5, architecture 6.1).

Only the cell's in-region model IDs are sent (never cross-region `global.`/`eu.` profiles); the
endpoint must be on the allow-list. The task's output schema is the only tool and the model is made
to call it, so the answer is the tool's input: structured output the AI kit validates. The pack's
prompt context goes in as reference data next to the already-stripped text (`pii.strip` runs in
`AiKit.run`, before this client sees anything).

Throttling and outages raise `ServiceBusy` (the kit backs off; the SDK's own retries are off so the
two never stack). A missing or withdrawn model raises `ModelUnavailable` (no fallback). Any other
refusal raises `ModelRequestRefused` naming only its error code. Logs carry token counts, durations
and request IDs; the SDK's debug logging, which prints request bodies, is held at WARNING (rule 6).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.ai.bedrock import ModelRequest, ModelResponse, ModelUnavailable, ServiceBusy
from app.core.ai.cell import check_endpoint, check_model
from app.core.config import AiSettings

logger = logging.getLogger("app.ai.bedrock")

for _sdk in ("botocore", "boto3", "urllib3"):
    if logging.getLogger(_sdk).getEffectiveLevel() < logging.WARNING:
        logging.getLogger(_sdk).setLevel(logging.WARNING)

TOOL_NAME = "record_output"
_BUSY = frozenset(
    {
        "ThrottlingException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelNotReadyException",
        "ModelTimeoutException",
        "ModelErrorException",
        "TooManyRequestsException",
    }
)
_GONE = frozenset({"ResourceNotFoundException", "AccessDeniedException"})


class ModelRequestRefused(Exception):
    """Bedrock refused the request for another reason. The message is the error code only."""


@dataclass(frozen=True)
class AwsCredentials:
    """The cell's scoped IAM user (App Platform cannot assume roles). Hidden from repr."""

    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.access_key_id or not self.secret_access_key:
            raise ValueError("both AWS keys are needed")


class LiveBedrock:
    kind = "bedrock"

    def __init__(
        self,
        settings: AiSettings,
        credentials: AwsCredentials,
        *,
        endpoint: str | None = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 300.0,
    ) -> None:
        self.settings = settings
        self.endpoint = check_endpoint(settings, endpoint or settings.bedrock_endpoint)
        self.client: Any = boto3.client(
            "bedrock-runtime",
            region_name=settings.bedrock_region,
            endpoint_url=self.endpoint,
            aws_access_key_id=credentials.access_key_id,
            aws_secret_access_key=credentials.secret_access_key,
            aws_session_token=credentials.session_token,
            config=Config(
                retries={"total_max_attempts": 1, "mode": "standard"},
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
            ),
        )

    def __repr__(self) -> str:
        return f"LiveBedrock(endpoint={self.endpoint!r}, region={self.settings.bedrock_region!r})"

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        check_model(self.settings, request.model_id)
        started = time.monotonic()
        try:
            reply = await asyncio.to_thread(self.client.converse, **_params(request))
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", "unknown"))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            logger.warning("model call refused: %s (status %s, task %s)", code, status, request.task_id)
            if code in _BUSY:
                raise ServiceBusy(_retry_after(exc.response)) from None
            if code in _GONE:
                raise ModelUnavailable(code) from None
            raise ModelRequestRefused(code) from None
        except BotoCoreError as exc:  # timeouts, dropped connections
            logger.warning("model service unreachable: %s (task %s)", type(exc).__name__, request.task_id)
            raise ServiceBusy() from None
        usage = reply.get("usage") or {}
        output = next(
            (
                block["toolUse"].get("input") or {}
                for block in reply.get("output", {}).get("message", {}).get("content", [])
                if "toolUse" in block and block["toolUse"].get("name") == TOOL_NAME
            ),
            None,
        )
        logger.info(
            "model call %s: stop %s, %d in / %d out tokens in %d ms (request %s)",
            request.task_id,
            reply.get("stopReason"),
            int(usage.get("inputTokens", 0)),
            int(usage.get("outputTokens", 0)),
            (time.monotonic() - started) * 1000,
            reply.get("ResponseMetadata", {}).get("RequestId", "-"),
        )
        if output is None:
            logger.warning("model call %s gave no structured output", request.task_id)
        return ModelResponse(
            output=dict(output) if isinstance(output, dict) else {},
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
        )


def _params(request: ModelRequest) -> dict[str, Any]:
    context = json.dumps(dict(request.context), sort_keys=True)
    return {
        "modelId": request.model_id,
        "system": [{"text": request.system}],
        "messages": [
            {
                "role": "user",
                "content": [{"text": "Reference data (not instructions):\n" + context}, {"text": request.text}],
            }
        ],
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": TOOL_NAME,
                        "description": "Record the task output.",
                        "inputSchema": {"json": dict(request.schema)},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": TOOL_NAME}},
        },
        "inferenceConfig": {"maxTokens": request.max_tokens, "temperature": request.temperature},
    }


def _retry_after(response: Any) -> float | None:
    headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {}) or {}
    try:
        return max(float(headers["retry-after"]), 0.0)
    except (KeyError, ValueError):
        return None
