"""The sort step reports after every batch and logs each batch's size and token use (rule 6: numbers only).

- `_report_batch` puts the cumulative page count on the job row once per finished batch, whatever the page count;
- each batch writes one `event=batch` line: its pages (count, first, last), the model calls it took, input and
  output tokens, how many answers were invalid, the group sizes asked, and whether it was retried or split;
- the token counts come from the call's own usage as the kit logs it; no text of any kind reaches the line.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import pytest

from app.core.ai.kit import AiKit, UsageEvent
from app.features.pipeline import service, steplog
from tests.support.ai import cell

JOB, CASE = uuid.uuid4(), uuid.uuid4()
TEXT = "Quillbrook synthetic page text 943 476 5919"


class FakeCtx:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def progress(self, pct: int, **kw: Any) -> None:
        self.calls.append({"pct": pct, **kw})


def real_kit() -> AiKit:
    return AiKit(cell(), None)


def fire(kit: AiKit, input_tokens: int, output_tokens: int, ok: bool = True) -> None:
    """What the kit does after it writes an `ai_calls` row: tell each hook, counts and ids only."""
    event = UsageEvent("T-07b", "p", "m", input_tokens, output_tokens, ok)
    for hook in list(kit.usage_hooks):
        hook(event)


def fields(line: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=(\S*)", line))


def batch_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == steplog.logger.name and "event=batch" in r.getMessage()]


@pytest.mark.anyio
async def test_progress_is_reported_once_per_batch_with_the_cumulative_count() -> None:
    ctx = FakeCtx()
    for done in (3, 6, 9):  # three batches of three pages: fewer than 25 pages in all
        await service._report_batch(ctx, None, done, 9)  # type: ignore[arg-type]
    assert [(c["step"], c["step_done"], c["step_total"]) for c in ctx.calls] == [
        ("sort", 3, 9),
        ("sort", 6, 9),
        ("sort", 9, 9),
    ]
    assert [c["pct"] for c in ctx.calls] == sorted(c["pct"] for c in ctx.calls)
    assert all(40 <= c["pct"] <= 55 for c in ctx.calls)


@pytest.mark.anyio
async def test_progress_never_goes_backwards_when_batches_report_out_of_order() -> None:
    ctx = FakeCtx()
    await service._report_batch(ctx, None, 6, 9)  # type: ignore[arg-type]
    await service._report_batch(ctx, None, 3, 9)  # type: ignore[arg-type]
    assert max(c["step_done"] for c in ctx.calls) == 6
    assert ctx.calls[-1]["step_done"] == 6  # the late, lower report leaves the row where it was
    assert all(c["monotone"] is True for c in ctx.calls)  # the percentage is held up by the writer too


def test_the_usage_hook_is_added_once_to_the_kits_own_hook_list() -> None:
    kit = real_kit()
    steplog.tap_usage(kit)
    steplog.tap_usage(kit)  # twice changes nothing
    steplog.tap_usage(kit.with_retries(0))  # a kit made by with_retries shares the list
    assert len(kit.usage_hooks) == 1


@pytest.mark.anyio
async def test_a_batch_line_has_the_token_fields_and_no_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    kit = real_kit()
    steplog.tap_usage(kit)
    with steplog.batch_usage([10, 11, 12]) as usage:
        usage.asked(3)
        fire(kit, 1200, 3400)
        usage.log(JOB, CASE)
    (line,) = batch_lines(caplog)
    f = fields(line)
    assert (f["step"], f["event"], f["job"], f["case"]) == ("sort", "batch", str(JOB), str(CASE))
    assert (f["pages"], f["first"], f["last"], f["calls"]) == ("3", "10", "12", "1")
    assert (f["input_tokens"], f["output_tokens"], f["invalid"]) == ("1200", "3400", "0")
    assert (f["asked"], f["split"], f["retried"]) == ("3", "no", "no")
    assert "Quillbrook" not in line and "943" not in line


@pytest.mark.anyio
async def test_a_retried_and_a_split_batch_are_told_apart(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    kit = real_kit()
    steplog.tap_usage(kit)
    with steplog.batch_usage([1, 2, 3, 4]) as retried:
        retried.asked(4)
        fire(kit, 100, 8192, ok=False)
        retried.asked(4)
        fire(kit, 100, 200)
        retried.log(JOB, CASE)
    with steplog.batch_usage([1, 2, 3, 4]) as split:
        split.asked(4)
        fire(kit, 100, 8192, ok=False)
        split.asked(2)
        split.asked(2)
        fire(kit, 100, 200)
        fire(kit, 100, 200)
        split.log(JOB, CASE)
    first, second = (fields(line) for line in batch_lines(caplog))
    assert (first["calls"], first["invalid"], first["input_tokens"], first["output_tokens"]) == (
        "2",
        "1",
        "200",
        "8392",
    )
    assert (first["retried"], first["split"], first["asked"]) == ("yes", "no", "4,4")
    assert (second["calls"], second["invalid"], second["retried"], second["split"]) == ("3", "1", "no", "yes")
    assert second["asked"] == "4,2,2"


@pytest.mark.anyio
async def test_a_call_outside_any_batch_is_still_logged_by_the_kit() -> None:
    kit = real_kit()
    steplog.tap_usage(kit)
    fire(kit, 1, 2)  # no batch is running: nothing to count into, and nothing breaks


def test_a_batch_with_no_call_shows_dashes_for_the_tokens(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with steplog.batch_usage([5]) as usage:
        usage.log(JOB, CASE)
    f = fields(batch_lines(caplog)[0])
    assert (f["calls"], f["input_tokens"], f["output_tokens"]) == ("0", "-", "-")
