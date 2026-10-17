"""The answer book: recorded model answers keyed by task and subject (architecture 6.1, 8.3).

Pipeline recordings (T-07b, T-07c) are written by the synthetic-bundle generator from its expected
answers, before any prompt wording exists, so they cannot be keyed by the whole prompt. A call's
key is its `task_id` and `subject`: the SHA-256 of what the call is about (`page_subject` for a
page's words with their IDs, `reference_subject` for one reference on a page). The prompt version
is left out: an ideal answer does not depend on the prompt's wording.

A key may hold several answers: the nth call gets the nth, later calls repeat the last, so a
recorded retry (an invalid first answer) can be planted. A call with no answer is refused.
Stored at `<root>/answer/<name>.json` as `{"kind": "answer", "request": {"task_id", "subject"},
"response": {"responses": [{"output", "input_tokens", "output_tokens"}, ...]}}`.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.cell import check_endpoint, check_model
from app.core.ai.recordings import FixtureStore, RecordingMissing
from app.core.config import AiSettings

KIND = "answer"


def _sha(value: object) -> str:
    canonical = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def page_subject(words: Iterable[tuple[str, str]]) -> str:
    """A page's subject: its words with their IDs, in reading order."""
    return _sha([[word_id, text] for word_id, text in words])


def reference_subject(page: str, word_ids: Sequence[str]) -> str:
    """One reference's subject: the page it is on and the words that make it up."""
    return _sha({"page": page, "word_ids": list(word_ids)})


def _key(task_id: str, subject: str) -> dict[str, str]:
    return {"task_id": task_id, "subject": subject}


class AnswerBook:
    """A `BedrockClient` stand-in that replays answers by task key."""

    def __init__(self, settings: AiSettings, store: FixtureStore, endpoint: str | None = None) -> None:
        self.settings = settings
        self.store = store
        self.endpoint = check_endpoint(settings, endpoint or settings.bedrock_endpoint)
        self.requests: list[ModelRequest] = []
        self._calls: Counter[tuple[str, str]] = Counter()

    def record(self, task_id: str, subject: str, responses: Sequence[ModelResponse], name: str | None = None) -> None:
        if not responses:
            raise ValueError("an answer needs at least one response")
        self.store.record(KIND, _key(task_id, subject), {"responses": [asdict(r) for r in responses]}, name)

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        check_model(self.settings, request.model_id)
        self.requests.append(request)
        if request.task_id is None or request.subject is None:
            raise RecordingMissing(f"no recorded {KIND} response for a call without a task key")
        key = (request.task_id, request.subject)
        responses = self.store.lookup(KIND, _key(*key))["responses"]
        n = self._calls[key]
        self._calls[key] += 1
        data = responses[min(n, len(responses) - 1)]
        return ModelResponse(
            output=data["output"], input_tokens=data["input_tokens"], output_tokens=data["output_tokens"]
        )
