"""AUDIT-14 M10 (kit side): a call with no case can be held to a per-person allowance for the day.

The route that has no case (the template mapping suggestion) passes `person_token_limit`; the kit counts the
caller's own case-less `ai_calls` rows of the last day (row-level security shows a person only their own) and
pauses the call with `ProcessingPaused("cost_guard")` once the limit is used. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import psycopg
import pytest

from app.core.ai.kit import AiKit
from app.core.db import Database
from app.core.errors import ProcessingPaused
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.ai_audit import PACK, VALID, FakeConn, task
from tests.support.db import scope_for
from tests.support.seed import Seed

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


# --- the real query, against Postgres as the restricted role (AUDIT-14 round 2, D) -------------------------------

LIMIT = 1_000


def add_call(
    admin: psycopg.Connection, person: uuid.UUID, tokens: int, *, case: uuid.UUID | None = None, age_hours: int = 0
) -> None:
    admin.execute(
        "insert into app.ai_calls (case_id, person_id, task_id, model_id, region, prompt_version, input_tokens,"
        " output_tokens, outcome, at) values (%s, %s, 'T-test', 'm', 'zz', 'v1', %s, %s, 'ok', %s)",
        (case, person, tokens - tokens // 2, tokens // 2, dt.datetime.now(dt.UTC) - dt.timedelta(hours=age_hours)),
    )


async def ask_as(api_db: Database, person: uuid.UUID, model: ScriptedBedrock) -> Any:
    async with api_db.request(scope_for(person)) as conn:
        return await AiKit(cell(), model).run(conn, task(), pack=PACK, text="t", person_token_limit=LIMIT)


@pytest.mark.parametrize(("used", "refused"), [(LIMIT, True), (LIMIT + 5_000, True), (LIMIT - 1, False)])
async def test_D_the_persons_own_case_less_rows_of_the_last_day_are_counted(
    used: int, refused: bool, api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    me = seed.person()
    add_call(admin, me, used - 100)
    add_call(admin, me, 100, age_hours=3)  # still inside the day: counted with the first
    model = ScriptedBedrock([response(VALID)])
    if refused:
        with pytest.raises(ProcessingPaused) as caught:
            await ask_as(api_db, me, model)
        assert caught.value.reason == "cost_guard" and model.requests == []
    else:
        assert await ask_as(api_db, me, model) == VALID and len(model.requests) == 1
        count = admin.execute("select count(*) from app.ai_calls where person_id = %s", (me,)).fetchone()
        assert count == (3,), "the allowed call was recorded as one more case-less row"


async def test_D_rows_of_another_person_a_case_or_an_earlier_day_are_not_counted(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    me, other = seed.person(), seed.person()
    case = seed.case(seed.org(members={me: "owner"}), me)
    add_call(admin, me, 100)  # the only row that counts, and it is far under the limit
    add_call(admin, other, 10 * LIMIT)  # somebody else's day
    add_call(admin, me, 10 * LIMIT, case=case)  # my own case work has its own guard (`app.ai_budget`)
    add_call(admin, me, 10 * LIMIT, age_hours=25)  # yesterday
    model = ScriptedBedrock([response(VALID)])
    assert await ask_as(api_db, me, model) == VALID and len(model.requests) == 1
    # control: the same limit is reached by the person's own recent case-less rows, so the query does count something
    add_call(admin, me, LIMIT)
    with pytest.raises(ProcessingPaused):
        await ask_as(api_db, me, ScriptedBedrock([response(VALID)]))


# --- a call with no case needs a guard in every entry point (AUDIT-14 round 2, E) --------------------------------


async def test_E_run_image_and_run_detached_refuse_a_call_with_no_case_before_any_provider_call() -> None:
    model = ScriptedBedrock([response(VALID)])
    kit = AiKit(cell(), model)
    anything: Any = object()  # the guard comes first: nothing about the picture or the database is looked at
    with pytest.raises(ValueError, match="case"):
        await kit.run_image(FakeConn(), task(), anything, pack=PACK)
    with pytest.raises(ValueError, match="case"):
        await kit.run_detached(anything, None, task(), pack=PACK, text="t", job_id=uuid.uuid4())  # type: ignore[arg-type]
    assert model.requests == []


async def test_E_with_a_case_the_same_calls_get_past_that_guard() -> None:
    kit = AiKit(cell(), ScriptedBedrock([response(VALID)]))
    anything: Any = object()
    with pytest.raises(TypeError):  # control: the next check (the picture is not a masked one) is reached
        await kit.run_image(FakeConn(), task(), anything, pack=PACK, case_id=uuid.uuid4())
