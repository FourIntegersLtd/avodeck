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
from types import SimpleNamespace
from typing import Any

import pytest

from app.features.pipeline import service, steplog

JOB, CASE = uuid.uuid4(), uuid.uuid4()
TEXT = "Quillbrook synthetic page text 943 476 5919"


class FakeCtx:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def progress(self, pct: int, **kw: Any) -> None:
        self.calls.append({"pct": pct, **kw})


class FakeKit:
    def __init__(self) -> None:
        self.logged: list[str] = []

    async def _log(self, conn: Any, task: Any, model_id: str, case_id: Any, response: Any, outcome: str) -> None:
        self.logged.append(outcome)


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
async def test_a_batch_line_has_the_token_fields_and_no_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    kit = FakeKit()
    steplog.tap_usage(kit)
    steplog.tap_usage(kit)  # twice changes nothing
    response = SimpleNamespace(input_tokens=1200, output_tokens=3400, output={"pages": [{"note": TEXT}]})
    with steplog.batch_usage([10, 11, 12]) as usage:
        usage.asked(3)
        await kit._log(None, SimpleNamespace(task_id="T-07b"), "m", CASE, response, "ok")
        usage.log(JOB, CASE)
    assert kit.logged == ["ok"]  # the kit's own logging still happens, once
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
    kit = FakeKit()
    steplog.tap_usage(kit)
    ok = SimpleNamespace(input_tokens=100, output_tokens=200, output={})
    bad = SimpleNamespace(input_tokens=100, output_tokens=8192, output={})
    with steplog.batch_usage([1, 2, 3, 4]) as retried:
        retried.asked(4)
        await kit._log(None, None, "m", CASE, bad, "invalid_output")
        retried.asked(4)
        await kit._log(None, None, "m", CASE, ok, "ok")
        retried.log(JOB, CASE)
    with steplog.batch_usage([1, 2, 3, 4]) as split:
        split.asked(4)
        await kit._log(None, None, "m", CASE, bad, "invalid_output")
        split.asked(2)
        split.asked(2)
        await kit._log(None, None, "m", CASE, ok, "ok")
        await kit._log(None, None, "m", CASE, ok, "ok")
        split.log(JOB, CASE)
    first, second = (fields(line) for line in batch_lines(caplog))
    assert (first["calls"], first["invalid"], first["input_tokens"], first["output_tokens"]) == ("2", "1", "200", "8392")
    assert (first["retried"], first["split"], first["asked"]) == ("yes", "no", "4,4")
    assert (second["calls"], second["invalid"], second["retried"], second["split"]) == ("3", "1", "no", "yes")
    assert second["asked"] == "4,2,2"


@pytest.mark.anyio
async def test_a_call_outside_any_batch_is_still_logged_by_the_kit() -> None:
    kit = FakeKit()
    steplog.tap_usage(kit)
    await kit._log(None, None, "m", CASE, SimpleNamespace(input_tokens=1, output_tokens=2, output={}), "ok")
    assert kit.logged == ["ok"]


def test_a_batch_with_no_call_shows_dashes_for_the_tokens(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with steplog.batch_usage([5]) as usage:
        usage.log(JOB, CASE)
    f = fields(batch_lines(caplog)[0])
    assert (f["calls"], f["input_tokens"], f["output_tokens"]) == ("0", "-", "-")
