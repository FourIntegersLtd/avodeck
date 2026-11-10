"""AUDIT-14 M10 (kit side): a call with no case can be held to a per-person allowance for the day.

The route that has no case (the template mapping suggestion) passes `person_token_limit`; the kit counts the
caller's own case-less `ai_calls` rows of the last day (row-level security shows a person only their own) and
pauses the call with `ProcessingPaused("cost_guard")` once the limit is used. Synthetic data only.
"""

from __future__ import annotations

import pytest

from app.core.ai.kit import AiKit
from app.core.errors import ProcessingPaused
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.ai_audit import PACK, VALID, FakeConn, task

pytestmark = pytest.mark.anyio


async def test_a_call_with_no_case_is_refused_once_the_persons_day_is_used_up() -> None:
    model = ScriptedBedrock([response(VALID)])
    conn = FakeConn(person_tokens=50_000)
    with pytest.raises(ProcessingPaused):
        await AiKit(cell(), model).run(conn, task(), pack=PACK, text="t", person_token_limit=50_000)
    assert model.requests == [], "the model was not asked"
    assert any("interval '1 day'" in sql for sql in conn.sql), "the allowance was counted from the person's own rows"


async def test_a_call_under_the_limit_goes_through_and_is_recorded() -> None:
    model = ScriptedBedrock([response(VALID)])
    conn = FakeConn(person_tokens=49_999)
    out = await AiKit(cell(), model).run(conn, task(), pack=PACK, text="t", person_token_limit=50_000)
    assert out == VALID and len(model.requests) == 1 and conn.outcomes == ["ok"]


async def test_without_the_parameter_nothing_is_counted_so_every_other_caller_is_unchanged() -> None:
    model = ScriptedBedrock([response(VALID)])
    conn = FakeConn(person_tokens=10**9)
    await AiKit(cell(), model).run(conn, task(), pack=PACK, text="t")
    assert not any("interval '1 day'" in sql for sql in conn.sql)


async def test_a_case_call_is_not_held_to_the_person_allowance() -> None:
    import uuid

    model = ScriptedBedrock([response(VALID)])
    conn = FakeConn(person_tokens=10**9)
    await AiKit(cell(), model).run(conn, task(), pack=PACK, text="t", case_id=uuid.uuid4(), person_token_limit=1)
    assert not any("interval '1 day'" in sql for sql in conn.sql), "a case has its own guard (`app.ai_budget`)"
