"""The question-set lock ignores oral instructions (core/contracts/questions.py; slice C review finding 4).

Oral instructions are recorded as an instruction version of kind `oral` so their words have a home, but
they carry no questions: a newer oral version must not reopen a locked set, or slices D, E and F would
stop with `questions_not_confirmed` (even on a served case) with nothing for the expert to confirm.
A newer letter of any other kind still reopens the set.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from tests.support.contracts import add_instruction_version
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    return {"expert": expert, "case": seed.case(org, expert, status="active")}


async def locked(api_db: Database, person: uuid.UUID, case: uuid.UUID) -> bool:
    from app.core.contracts.questions import questions_locked

    async with api_db.request(scope_for(person)) as conn:
        return await questions_locked(conn, case)


async def test_an_oral_instruction_does_not_reopen_the_questions(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    first = add_instruction_version(admin, team["case"], version_no=1)
    admin.execute("update app.instruction_versions set locked_at = now() where id = %s", (first,))
    add_instruction_version(admin, team["case"], version_no=2, kind="oral")
    assert await locked(api_db, team["expert"], team["case"]) is True
    add_instruction_version(admin, team["case"], version_no=3, kind="supplementary")
    assert await locked(api_db, team["expert"], team["case"]) is False, "a newer letter still reopens them"


async def test_oral_instructions_alone_never_lock_the_questions(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    add_instruction_version(admin, team["case"], version_no=1, kind="oral")
    assert await locked(api_db, team["expert"], team["case"]) is False
