"""The model client interface and its recorded stand-in (architecture 6.1).

The live client (AWS Bedrock Converse, the cell's in-region model IDs only) is
`bedrock_live.LiveBedrock`; `clients.live_or_recorded` picks it or this stand-in. Every client
refuses an endpoint off the cell's allow-list when it is made and a model ID off it before anything
is sent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, fields
from typing import TYPE_CHECKING, Any, Protocol, Self, runtime_checkable

from app.core.ai.cell import check_endpoint, check_model
from app.core.ai.recordings import FixtureStore
from app.core.config import AiSettings

if TYPE_CHECKING:
    from app.core.ai.mask import MaskedImage


@dataclass(frozen=True)
class ModelRequest:
    """One structured-output call. `text` is already through `pii.strip`; hidden from repr (rule 6).

    `task_id` and `subject` are the call's stable task key (`app.core.ai.answers`): never sent to the
    model and not part of a whole-prompt recording's key.
    """

    model_id: str
    system: str = field(repr=False)
    context: Mapping[str, Any] = field(repr=False)
    text: str = field(repr=False)
    schema: Mapping[str, Any] = field(repr=False)
    temperature: float = 0.0
    max_tokens: int = 4096
    task_id: str | None = None
    subject: str | None = None
    image_sha256: str | None = None  # a masked page picture (T-10): its hash is part of the recording key
    image: MaskedImage | None = field(default=None, repr=False, compare=False)  # never recorded, never logged


@dataclass(frozen=True)
class ModelResponse:
    output: dict[str, Any] = field(repr=False)
    input_tokens: int
    output_tokens: int
    model_id: str | None = None  # the model that answered, when not the one asked for (an overflow model, ADR 0036)


_SAFE_FIELD = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

# "2/4" while a kit makes a direct attempt, so the client's one log line can say which attempt it was
# (ADR 0036). Set by `kit.AiKit` right before the call; empty for every other caller.
CALL_ATTEMPT: ContextVar[str | None] = ContextVar("ai_call_attempt", default=None)


class ModelCallFailure(Exception):
    """A model call that did not give an answer. Carries only typed, vetted facts for the logs: the HTTP
    status, the vendor's error type and code, its request ID and the underlying exception class. Never
    the message or the body, which can echo the input (rule 6)."""

    status: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    request_id: str | None = None
    cause: str | None = None

    def annotated(
        self,
        *,
        status: int | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
        cause: str | None = None,
    ) -> Self:
        def safe(value: str | None) -> str | None:
            return value if value is not None and _SAFE_FIELD.match(value) else None

        self.status = status
        self.error_type = safe(error_type)
        self.error_code = safe(error_code)
        self.request_id = safe(request_id)
        self.cause = safe(cause)
        return self

    def log_fields(self) -> str:
        """`key=value` pairs naming the failure for a log line: class, cause, status, error type, code, request ID."""
        return (
            f"error_class={type(self).__name__} cause={self.cause or '-'} status={self.status or '-'}"
            f" error_type={self.error_type or '-'} error_code={self.error_code or '-'}"
            f" request_id={self.request_id or '-'}"
        )


class ServiceBusy(ModelCallFailure):
    """Throttled or a temporary outage (429/5xx). `retry_after` in seconds when the service says."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("model service busy")
        self.retry_after = retry_after


class ModelUnavailable(ModelCallFailure):
    """The in-region model is gone or withdrawn (F24). Never retried elsewhere."""


class ModelRequestRefused(ModelCallFailure):
    """The model service refused the request for another reason. The message is the error code only."""


@runtime_checkable
class BedrockClient(Protocol):
    endpoint: str

    async def invoke(self, request: ModelRequest) -> ModelResponse: ...


_NOT_RECORDED = ("task_id", "subject", "image")


def _request_record(request: ModelRequest) -> dict[str, Any]:
    record = {f.name: getattr(request, f.name) for f in fields(request) if f.name not in _NOT_RECORDED}
    if request.image_sha256 is None:
        del record["image_sha256"]  # keeps every recording made before pictures existed
    record["context"] = dict(request.context)
    record["schema"] = dict(request.schema)
    return record


class RecordedBedrock:
    """Replays recorded model responses; refuses any request it has no recording for."""

    kind = "bedrock"

    def __init__(self, settings: AiSettings, store: FixtureStore, endpoint: str | None = None) -> None:
        self.settings = settings
        self.store = store
        self.endpoint = check_endpoint(settings, endpoint or settings.bedrock_endpoint)
        self.requests: list[ModelRequest] = []

    def record(self, request: ModelRequest, response: ModelResponse, name: str | None = None) -> None:
        check_model(self.settings, request.model_id)
        recorded = asdict(response)
        if recorded["model_id"] is None:
            del recorded["model_id"]  # keeps every recording made before this field existed
        self.store.record(self.kind, _request_record(request), recorded, name)

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        check_model(self.settings, request.model_id)
        self.requests.append(request)
        data = self.store.lookup(self.kind, _request_record(request))
        return ModelResponse(
            output=data["output"], input_tokens=data["input_tokens"], output_tokens=data["output_tokens"]
        )
