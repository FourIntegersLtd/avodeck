"""`AiKit.usage_hooks`: the public place for a slice to see each AI call's usage, instead of wrapping `_log`.
A hook gets token counts and ids only (rule 6)."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest

from app.core.ai.kit import AiKit, AiTask, UsageEvent
from app.core.ai.bedrock import ModelResponse
from tests.support.ai import cell

pytestmark = pytest.mark.anyio

TASK = AiTask(task_id="T-hook", prompt_version="v1", model="sonnet", system="s", schema={"type": "object"})


class Conn:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def execute(self, sql: str, params: Any = None) -> None:
        self.rows.append(params)


async def test_a_hook_is_called_once_per_logged_call_with_counts_and_ids_only() -> None:
    kit = AiKit(cell(), None)
    seen: list[UsageEvent] = []
    kit.usage_hooks.append(seen.append)
    response = ModelResponse(output={"secret": "patient text"}, input_tokens=120, output_tokens=7)
    await kit._log(Conn(), TASK, "model-x", uuid.uuid4(), response, "ok")  # type: ignore[arg-type]
    [event] = seen
    assert (event.task_id, event.model_id, event.input_tokens, event.output_tokens) == ("T-hook", "model-x", 120, 7)
    assert event.ok is True
    assert event.provider == cell().bedrock_region
    assert "patient" not in repr(event)
    assert {f.name for f in dataclasses.fields(event)} == {
        "task_id",
        "provider",
        "model_id",
        "input_tokens",
        "output_tokens",
        "ok",
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.ok = False  # type: ignore[misc]


async def test_an_invalid_answer_is_reported_as_not_ok_and_a_failing_hook_does_not_break_the_call() -> None:
    kit = AiKit(cell(), None)
    seen: list[UsageEvent] = []

    def broken(event: UsageEvent) -> None:
        raise RuntimeError("boom")

    kit.usage_hooks.extend([broken, seen.append])
    response = ModelResponse(output={}, input_tokens=1, output_tokens=2)
    await kit._log(Conn(), TASK, "m", None, response, "invalid_output")  # type: ignore[arg-type]
    assert [e.ok for e in seen] == [False]


def test_a_kit_made_by_with_retries_shares_the_hooks() -> None:
    kit = AiKit(cell(), None)
    kit.usage_hooks.append(lambda event: None)
    assert kit.with_retries(0).usage_hooks is kit.usage_hooks
