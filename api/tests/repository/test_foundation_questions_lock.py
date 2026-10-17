"""The question-set lock contract (core/contracts/questions.py; state-machines.md "Question").

`instruction_versions` is a contract table: slice C records each letter of instruction (and later
supplementary or oral instructions) as a numbered version and locks the questions on the latest
one. Slices D, E and F ask `questions_locked(conn, case_id)` or depend on
`require_questions_locked()` (409 `questions_not_confirmed`) before they use the questions.

- No version, or the latest version unlocked: not locked. A newer version reopens the questions.
- Locking needs `question.confirm` (the named expert); a lock is never undone.
- `questions.instruction_version_id` must name a version of the case.
- A confirmed or edited question goes back to suggested only when it moves to a newer version.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.contracts.case_tables import CASE_TABLES
from app.core.db import Database
from app.core.errors import ApiError
from tests.support.contracts import add_instruction_version, add_question
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio
Refused = psycopg.errors.InsufficientPrivilege


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "viewer")}
    org = seed.org(kind="team", members={p["expert"]: "expert", p["assistant"]: "assistant", p["viewer"]: "viewer"})
    p["case"] = seed.case(org, p["expert"], status="active", members={p["assistant"]: "assistant", p["viewer"]: "viewer"})
    return p


async def locked(api_db: Database, person: uuid.UUID, case: uuid.UUID) -> bool:
    from app.core.contracts.questions import questions_locked

    async with api_db.request(scope_for(person)) as conn:
        return await questions_locked(conn, case)


def lock_as(api_dsn: str, person: uuid.UUID, version: uuid.UUID) -> int:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        return conn.execute("update app.instruction_versions set locked_at = now() where id = %s", (version,)).rowcount


def test_instruction_versions_is_a_case_table() -> None:
    assert "instruction_versions" in CASE_TABLES


async def test_a_case_with_no_instructions_is_not_locked(api_db: Database, team: dict[str, uuid.UUID]) -> None:
    assert await locked(api_db, team["expert"], team["case"]) is False


async def test_the_latest_version_decides(
    api_db: Database, api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    first = add_instruction_version(admin, team["case"], version_no=1)
    assert await locked(api_db, team["expert"], team["case"]) is False
    assert lock_as(api_dsn, team["expert"], first) == 1
    assert await locked(api_db, team["expert"], team["case"]) is True
    assert await locked(api_db, team["viewer"], team["case"]) is True, "anyone who can see the content can ask"
    add_instruction_version(admin, team["case"], version_no=2)
    assert await locked(api_db, team["expert"], team["case"]) is False, "new instructions reopen the questions"


def test_only_the_named_expert_locks_the_questions(
    api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    version = add_instruction_version(admin, team["case"])
    with pytest.raises(Refused):
        lock_as(api_dsn, team["assistant"], version)
    assert lock_as(api_dsn, team["viewer"], version) == 0, "a viewer cannot change the row at all"
    row = admin.execute("select locked_at from app.instruction_versions where id = %s", (version,)).fetchone()
    assert row == (None,)


def test_a_lock_records_who_locked_it_and_is_never_undone(
    api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    version = add_instruction_version(admin, team["case"])
    assert lock_as(api_dsn, team["expert"], version) == 1
    row = admin.execute("select locked_by from app.instruction_versions where id = %s", (version,)).fetchone()
    assert row == (team["expert"],)
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.instruction_versions set locked_at = null where id = %s", (version,))
    assert err.value.sqlstate == "AV409"


def test_an_assistant_records_a_new_instruction_version(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(team["assistant"]),))
        cur = conn.execute(
            "insert into app.instruction_versions (case_id, version_no, kind, received_on) values (%s, 1, 'letter', '2026-01-05')",
            (team["case"],),
        )
        assert cur.rowcount == 1


def test_a_new_version_cannot_start_locked(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    with pytest.raises(psycopg.Error) as err:
        admin.execute(
            "insert into app.instruction_versions (case_id, version_no, kind, locked_at) values (%s, 1, 'letter', now())",
            (team["case"],),
        )
    assert err.value.sqlstate == "AV409"


def test_a_question_names_a_real_instruction_version(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    question = add_question(admin, team["case"])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin.execute("update app.questions set instruction_version_id = %s where id = %s", (uuid.uuid4(), question))


@pytest.mark.parametrize("status", ["confirmed", "edited"])
def test_new_instructions_reopen_a_confirmed_question(
    status: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    first = add_instruction_version(admin, team["case"], version_no=1)
    second = add_instruction_version(admin, team["case"], version_no=2)
    question = add_question(admin, team["case"], status=status, instruction_version_id=first)
    admin.execute(
        "update app.questions set status = 'suggested', instruction_version_id = %s where id = %s", (second, question)
    )
    assert admin.execute("select status from app.questions where id = %s", (question,)).fetchone() == ("suggested",)


def test_a_question_is_not_reopened_without_newer_instructions(
    admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    first = add_instruction_version(admin, team["case"], version_no=1)
    second = add_instruction_version(admin, team["case"], version_no=2)
    question = add_question(admin, team["case"], status="confirmed", instruction_version_id=second)
    for change in ("status = 'suggested'", f"status = 'suggested', instruction_version_id = '{first}'"):
        with pytest.raises(psycopg.Error) as err:
            admin.execute(f"update app.questions set {change} where id = %s", (question,))
        assert err.value.sqlstate == "AV409"


async def test_the_gate_refuses_until_the_questions_are_locked(
    api_db: Database, api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    from app.core.contracts.questions import require_questions_locked

    gate = require_questions_locked()
    assert gate.case_action == "case.view_content"
    version = add_instruction_version(admin, team["case"])
    async with api_db.request(scope_for(team["expert"])) as conn:
        with pytest.raises(ApiError) as err:
            await gate(None, team["case"], conn)  # type: ignore[arg-type]
    assert (err.value.status, err.value.code) == (409, "questions_not_confirmed")
    lock_as(api_dsn, team["expert"], version)
    async with api_db.request(scope_for(team["expert"])) as conn:
        access = await gate(None, team["case"], conn)  # type: ignore[arg-type]
    assert "question.confirm" in access.allowed_actions


async def test_the_gate_answers_404_to_someone_who_cannot_see_the_case(api_db: Database, seed: Seed, team: dict[str, uuid.UUID]) -> None:
    from app.core.contracts.questions import require_questions_locked

    async with api_db.request(scope_for(seed.person())) as conn:
        with pytest.raises(ApiError) as err:
            await require_questions_locked()(None, team["case"], conn)  # type: ignore[arg-type]
    assert err.value.status == 404
