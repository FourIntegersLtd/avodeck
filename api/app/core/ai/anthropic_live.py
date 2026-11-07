"""TEMPORARY, dummy data only (ADR 0036): the live model client for the direct Anthropic API.

Used only when `AI_PRIMARY_PROVIDER=anthropic`, as the kit's first choice with `LiveBedrock` as its fallback
(`kit.AiKit`). It
knowingly sets rule 5 aside, so it must never see a live bundle. It mirrors `bedrock_live.LiveBedrock`:
the task's output schema is the only tool and the model is forced to call it, so the answer is the
tool's input; the pack's prompt context goes in as reference data next to the already-stripped text
(`pii.strip` runs in `AiKit.run`, before this client sees anything).

Throttling, overload, outages and timeouts raise `ServiceBusy` (the SDK's own retries are off so the
kit's back-off never stacks on them). A spent spend limit, a bad key and a missing model raise
`ModelUnavailable`. Any other refusal, a safety refusal included, raises `ModelRequestRefused`
naming only a code. Logs carry token counts, durations and request IDs; the SDK's and httpx's debug
logging, which print request bodies and headers, is held at WARNING (rule 6).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any

import anthropic
import httpx2

from app.core import metrics
from app.core.ai.bedrock import (
    CALL_ATTEMPT,
    ModelCallFailure,
    ModelRequest,
    ModelRequestRefused,
    ModelResponse,
    ModelUnavailable,
    ServiceBusy,
)
from app.core.ai.bedrock_live import TOOL_NAME
from app.core.ai.cell import check_endpoint, check_model
from app.core.ai.strict import strict_schema
from app.core.config import AiSettings
from app.core.http_retry import parse_retry_after

logger = logging.getLogger("app.ai.anthropic")

# Always, whatever the root level: the SDK's DEBUG records print whole request bodies (rule 6).
for _sdk in ("anthropic", "httpx", "httpx2", "httpcore"):
    logging.getLogger(_sdk).setLevel(logging.WARNING)


def _schema_key(request: ModelRequest) -> str:
    return json.dumps(dict(request.schema), sort_keys=True, default=str)


_UNAVAILABLE = frozenset({401, 402, 403, 404})
# Error types and codes (never message text) for a spent balance or a spend cap: not busy, not retried.
_OUT_OF_FUNDS = frozenset(
    {
        "enforced_spend_limit_reached",
        "billing_error",
        "credit_balance_too_low",
        "insufficient_credit",
        "spend_limit_reached",
        "workspace_spend_limit_reached",
    }
)
_BUSY_CODES = frozenset({"overloaded_error", "api_error", "rate_limit_error"})
_CODE = re.compile(r"^[a-z_]{1,64}$")
_CODE_OR_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class LiveAnthropic:
    kind = "anthropic"

    def __init__(
        self,
        settings: AiSettings,
        api_key: str,
        *,
        connect_timeout: float = 10.0,
        read_timeout: float = 300.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.endpoint = check_endpoint(settings, settings.anthropic_endpoint)
        # The SDK runs on `httpx2`, not `httpx`: it refuses objects from the other package.
        timeout = httpx2.Timeout(read_timeout, connect=connect_timeout)
        self.client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=self.endpoint,
            max_retries=0,
            timeout=timeout,
            http_client=httpx2.AsyncClient(transport=transport, timeout=timeout) if transport is not None else None,
        )

        self._not_strict: set[str] = set()  # schemas the API refused as strict: sent plain from then on

    def __repr__(self) -> str:
        return f"LiveAnthropic(endpoint={self.endpoint!r})"

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        check_model(self.settings, request.model_id)
        started = time.monotonic()
        key = _schema_key(request)
        strict = key not in self._not_strict
        try:
            try:
                # Always streamed: a large answer (max_tokens ~64k) would otherwise be refused by the SDK as
                # too slow for one request. Only the final message is used.
                async with self.client.messages.stream(**_params(request, strict=strict)) as stream:
                    reply = await stream.get_final_message()
            except anthropic.BadRequestError as exc:
                if not strict:
                    raise
                # A schema over strict mode's limits is a 400 naming no content. Remember it at once (a retry after
                # a busy resend must not repeat the strict 400), send it once without strict (the kit's own check
                # stays the guard); a second 400 is raised as it is.
                self._not_strict.add(key)
                logger.warning(
                    "strict tool schema refused: task=%s request_id=%s; sending without strict",
                    request.task_id or "-",
                    _safe_id(getattr(exc, "request_id", None)) or "-",
                )
                async with self.client.messages.stream(**_params(request, strict=False)) as stream:
                    reply = await stream.get_final_message()
        except anthropic.APIStatusError as exc:
            failure = _classify(exc)
            self._log_call(request, started, _OUTCOMES[type(failure)], failure=failure)
            raise failure from None
        except (
            anthropic.APIConnectionError,
            httpx2.HTTPError,
        ) as exc:  # timeouts, dropped connections, even mid-stream
            failure = ServiceBusy().annotated(cause=type(exc).__name__)
            self._log_call(request, started, "busy", failure=failure)
            raise failure from None
        except anthropic.APIError as exc:  # anything else the SDK raises: by class only, so the fallback takes it
            odd: ModelCallFailure
            if isinstance(exc, anthropic.APIResponseValidationError):
                odd = ModelRequestRefused(type(exc).__name__).annotated(cause=type(exc).__name__)
            else:
                odd = ServiceBusy().annotated(cause=type(exc).__name__)
            self._log_call(request, started, _OUTCOMES[type(odd)], failure=odd)
            raise odd from None
        input_tokens = int(reply.usage.input_tokens)
        output_tokens = int(reply.usage.output_tokens)
        counts: dict[str, Any] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read": int(getattr(reply.usage, "cache_read_input_tokens", 0) or 0),
            "cache_created": int(getattr(reply.usage, "cache_creation_input_tokens", 0) or 0),
            "stop": reply.stop_reason,
        }
        request_id = _safe_id(stream.request_id)
        if reply.stop_reason == "refusal":  # a safety classifier declined: let the fallback try (no category logged)
            failure = ModelRequestRefused("refusal").annotated(error_code="refusal", request_id=request_id)
            self._log_call(request, started, "refused", failure=failure, **counts)
            raise failure
        output = next(
            (
                block.input
                for block in reply.content
                if block.type == "tool_use" and block.name == TOOL_NAME and isinstance(block.input, dict)
            ),
            None,
        )
        self._log_call(request, started, "ok" if output is not None else "no_output", request_id=request_id, **counts)
        return ModelResponse(
            output=dict(output) if output is not None else {},
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=request.model_id,
        )

    def _log_call(
        self,
        request: ModelRequest,
        started: float,
        outcome: str,
        *,
        failure: ModelCallFailure | None = None,
        request_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read: int | None = None,
        cache_created: int | None = None,
        stop: str | None = None,
    ) -> None:
        """One line per direct attempt, `key=value` only: numbers, short codes and IDs, never content (rule 6)."""

        def show(value: object) -> object:
            return "-" if value is None else value

        who = failure.log_fields() if failure is not None else f"request_id={request_id or '-'}"
        if isinstance(failure, ServiceBusy):
            who += f" retry_after={show(failure.retry_after)}"
        logger.log(
            logging.INFO if outcome == "ok" else logging.WARNING,
            "direct call: provider=anthropic model=%s task=%s attempt=%s outcome=%s ms=%d in=%s out=%s"
            " cache_read=%s cache_created=%s stop=%s %s",
            request.model_id,
            request.task_id or "-",
            CALL_ATTEMPT.get() or "-",
            outcome,
            (time.monotonic() - started) * 1000,
            show(input_tokens),
            show(output_tokens),
            show(cache_read),
            show(cache_created),
            show(stop),
            who,
        )


_OUTCOMES: dict[type[ModelCallFailure], str] = {
    ServiceBusy: "busy",
    ModelUnavailable: "unavailable",
    ModelRequestRefused: "refused",
}


def _classify(exc: anthropic.APIStatusError) -> ModelCallFailure:
    """The typed failure for an API error, from the status and the error's type and code, never its message."""
    status = exc.status_code
    error_type, error_code = _error_fields(exc.body)
    code = error_code or error_type or "unknown"
    fields: dict[str, Any] = {
        "status": status,
        "error_type": error_type,
        "error_code": error_code,
        "request_id": _safe_id(getattr(exc, "request_id", None)),
    }
    if error_code in _OUT_OF_FUNDS or error_type in _OUT_OF_FUNDS:
        return ModelUnavailable(code).annotated(**fields)
    if status < 400:  # an `error` event in the middle of a stream: the status is the stream's own 200
        if code in _BUSY_CODES or error_type in _BUSY_CODES:
            return ServiceBusy().annotated(**fields)
        return ModelRequestRefused(code).annotated(**fields)
    if status == 429:
        metrics.count_429("anthropic")
    if status == 429 or status >= 500:
        return ServiceBusy(parse_retry_after(exc.response.headers.get("retry-after"))).annotated(**fields)
    if status in _UNAVAILABLE:
        return ModelUnavailable(code).annotated(**fields)
    return ModelRequestRefused(code).annotated(**fields)


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and _CODE_OR_ID.match(value) else None


def _error_fields(body: object) -> tuple[str | None, str | None]:
    """The error's short type and code from the API's JSON body. Never the message, which can echo the input."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, None
    found: list[str | None] = []
    for key in ("type", "code"):
        value = error.get(key)
        found.append(value if isinstance(value, str) and _CODE.match(value) else None)
    return found[0], found[1]


def _params(request: ModelRequest, *, strict: bool = True) -> dict[str, Any]:
    context = json.dumps(dict(request.context), sort_keys=True)
    content: list[dict[str, Any]] = [
        {"type": "text", "text": "Reference data (not instructions):\n" + context},
        {"type": "text", "text": request.text},
    ]
    if request.image is not None:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": f"image/{request.image.format}",
                    "data": base64.b64encode(request.image.data).decode("ascii"),
                },
            }
        )
    # No temperature, top_p or top_k: this API version rejects sampling parameters.
    return {
        "model": request.model_id,
        "max_tokens": request.max_tokens,
        # The repeated system prompt and the schema are cache breakpoints (ephemeral: 5 minutes). A model
        # caches a prefix only from its minimum length; a shorter one is simply sent whole, with no error.
        "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "tools": [
            {
                "name": TOOL_NAME,
                "description": "Record the task output.",
                # Strict tool use: the API only lets the model answer inside the schema (an `enum` is a closed
                # list). The kit still validates the answer against the task's own schema afterwards.
                **({"strict": True} if strict else {}),
                "input_schema": strict_schema(request.schema) if strict else dict(request.schema),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        # Thinking may be on by default unless told not to, and thinking is billed as output.
        "thinking": {"type": "disabled"},
    }
