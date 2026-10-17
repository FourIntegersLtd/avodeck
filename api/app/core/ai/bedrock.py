"""The model client interface and its recorded stand-in (architecture 6.1).

The real client (AWS Bedrock, the cell's in-region model IDs only) arrives with the cloud cell
and implements `BedrockClient`. Every client refuses an endpoint off the cell's allow-list when
it is made and a model ID off it before anything is sent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from app.core.ai.cell import check_endpoint, check_model
from app.core.ai.recordings import FixtureStore
from app.core.config import AiSettings


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


@dataclass(frozen=True)
class ModelResponse:
    output: dict[str, Any] = field(repr=False)
    input_tokens: int
    output_tokens: int


class ServiceBusy(Exception):
    """Throttled or a temporary outage (429/5xx). `retry_after` in seconds when the service says."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("model service busy")
        self.retry_after = retry_after


class ModelUnavailable(Exception):
    """The in-region model is gone or withdrawn (F24). Never retried elsewhere."""


class BedrockClient(Protocol):
    endpoint: str

    async def invoke(self, request: ModelRequest) -> ModelResponse: ...


def _request_record(request: ModelRequest) -> dict[str, Any]:
    record = asdict(request)
    del record["task_id"], record["subject"]
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
        self.store.record(self.kind, _request_record(request), asdict(response), name)

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        check_model(self.settings, request.model_id)
        self.requests.append(request)
        data = self.store.lookup(self.kind, _request_record(request))
        return ModelResponse(
            output=data["output"], input_tokens=data["input_tokens"], output_tokens=data["output_tokens"]
        )
