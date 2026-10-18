"""The confirmed-questions read contract (core/contracts/questions.py; state-machines.md "Question").

Slices D, E and F read the questions the named expert confirmed through `confirmed_questions(conn, case_id)`
instead of querying `app.questions`: confirmed and edited questions only, in question order, as `Question`
models whose `wording` is the confirmed text when edited and the exact words otherwise. It runs as the
caller under row-level security, so someone outside the case gets none.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.contracts.questions import Question, confirmed_questions
from app.core.db import Database
from tests.support.contracts import add_question
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "viewer", "outsider")}
    org = seed.org(kind="team", members={p["expert"]: "expert", p["viewer"]: "viewer"})
    p["case"] = seed.case(org, p["expert"], status="active", members={p["viewer"]: "viewer"})
    return p


async def read(api_db: Database, person: uuid.UUID, case: uuid.UUID) -> list[Question]:
    async with api_db.request(scope_for(person)) as conn:
        return await confirmed_questions(conn, case)


async def test_only_confirmed_and_edited_questions_in_order(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = team["case"]
    edited = add_question(admin, case, number=2, status="edited")
    admin.execute("update app.questions set confirmed_text = 'Was the standard met then?' where id = %s", (edited,))
    add_question(admin, case, number=3, status="suggested")
    confirmed = add_question(admin, case, number=1, status="confirmed")

    got = await read(api_db, team["expert"], case)
    assert [q.id for q in got] == [confirmed, edited]
    assert all(isinstance(q, Question) and q.case_id == case for q in got)
    assert [q.status for q in got] == ["confirmed", "edited"]
    assert got[0].wording == got[0].text_verbatim
    assert got[1].wording == "Was the standard met then?"
    assert got[1].text_verbatim != got[1].wording, "the exact words are kept"


async def test_case_members_read_them_and_outsiders_get_none(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    add_question(admin, team["case"], status="confirmed")
    assert len(await read(api_db, team["viewer"], team["case"])) == 1
    assert await read(api_db, team["outsider"], team["case"]) == []


async def test_a_case_with_no_confirmed_questions_has_none(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    add_question(admin, team["case"], status="suggested")
    assert await read(api_db, team["expert"], team["case"]) == []
