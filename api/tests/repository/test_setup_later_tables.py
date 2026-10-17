"""Slice C's later case tables (migrations 0320, 0330 and 0340; review finding 8).

Row-level security repeats the case access check (rule 9): people on the case see the rows their case role
allows, people outside it and the bare API role see nothing. Only the named expert writes conflict answers,
declarations, withdrawals and assumptions; the reading records (`instruction_extractions`, `prefill_runs`)
and the suggestions themselves are written only by the worker. Declarations, reading records and runs are
append-only, answers and suggestions are never deleted, and the guards keep an assumption's exact words
and a suggestion's content.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

INSERTS = {
    "conflict_answers": "insert into app.conflict_answers (case_id, question_key, answer) values (%(c)s, 'k', 'no')",
    "conflict_declarations": "insert into app.conflict_declarations (case_id, outcome) values (%(c)s, 'none')",
    "case_withdrawals": "insert into app.case_withdrawals (case_id, reason) values (%(c)s, '\\x00')",
    "assumptions": "insert into app.assumptions (case_id, instruction_version_id, number, text_verbatim)"
    " values (%(c)s, %(v)s, 1, 'Assume this')",
    "instruction_extractions": "insert into app.instruction_extractions (instruction_version_id, case_id, questions,"
    " assumptions, rejected, model_task, prompt_version) values (%(v)s, %(c)s, 1, 1, 0, 'T-06', 'p1')",
    "prefill_suggestions": "insert into app.prefill_suggestions (case_id, instruction_version_id, field, value,"
    " word_ids, model_task, prompt_version) values (%(c)s, %(v)s, 'party_role', '\\x00', '{w1}', 'T-04', 'p1')",
    "prefill_runs": "insert into app.prefill_runs (case_id, kept, rejected) values (%(c)s, 1, 0)",
}
EXPERT_WRITES = ("assumptions", "case_withdrawals", "conflict_answers", "conflict_declarations")
WORKER_ONLY = ("instruction_extractions", "prefill_runs", "prefill_suggestions")
NO_UPDATE = ("conflict_declarations", "case_withdrawals", "instruction_extractions", "prefill_runs")


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, assistant, viewer, outsider = (seed.person() for _ in range(4))
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant", viewer: "expert"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    row = admin.execute(
        "insert into app.instruction_versions (case_id, version_no, kind) values (%s, 1, 'letter') returning id",
        (case,),
    ).fetchone()
    assert row is not None
    return {
        "expert": expert,
        "assistant": assistant,
        "viewer": viewer,
        "outsider": outsider,
        "case": case,
        "version": row[0],
    }


def params(world: dict[str, uuid.UUID]) -> dict[str, uuid.UUID]:
    return {"c": world["case"], "v": world["version"]}


async def run_as(
    api_db: Database, person: uuid.UUID, sql: str, args: Mapping[str, Any] | None = None
) -> list[tuple[object, ...]]:
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute(sql, args)
        return await cur.fetchall() if cur.description else []


@pytest.mark.parametrize("table", sorted(INSERTS))
async def test_people_on_the_case_see_the_rows_and_others_do_not(
    api_db: Database, api_dsn: str, admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str
) -> None:
    admin.execute(INSERTS[table], params(world))
    count = f"select count(*) from app.{table}"  # noqa: S608
    assert await run_as(api_db, world["expert"], count) == [(1,)]
    assert await run_as(api_db, world["viewer"], count) == [(1,)]
    assert await run_as(api_db, world["outsider"], count) == [(0,)]
    with psycopg.connect(api_dsn) as bare:
        assert bare.execute(count).fetchone() == (0,)


@pytest.mark.parametrize("table", EXPERT_WRITES)
async def test_only_the_named_expert_writes(api_db: Database, world: dict[str, uuid.UUID], table: str) -> None:
    for person in ("assistant", "viewer", "outsider"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await run_as(api_db, world[person], INSERTS[table], params(world))
    await run_as(api_db, world["expert"], INSERTS[table], params(world))


@pytest.mark.parametrize("table", WORKER_ONLY)
async def test_reading_records_and_suggestions_come_only_from_the_worker(
    api_db: Database, world: dict[str, uuid.UUID], table: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["expert"], INSERTS[table], params(world))


@pytest.mark.parametrize("table", NO_UPDATE)
async def test_records_are_append_only(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str
) -> None:
    admin.execute(INSERTS[table], params(world))
    for sql in (f"update app.{table} set case_id = case_id", f"delete from app.{table}"):  # noqa: S608
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await run_as(api_db, world["expert"], sql)


@pytest.mark.parametrize("table", ["conflict_answers", "assumptions", "prefill_suggestions"])
async def test_answers_assumptions_and_suggestions_are_never_deleted(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str
) -> None:
    admin.execute(INSERTS[table], params(world))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["expert"], f"delete from app.{table}")  # noqa: S608


async def test_an_assumption_keeps_its_exact_words(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(INSERTS["assumptions"], params(world))
    with pytest.raises(psycopg.Error) as caught:
        await run_as(api_db, world["expert"], "update app.assumptions set text_verbatim = 'Other words'")
    assert caught.value.sqlstate == "AV409"
    await run_as(api_db, world["expert"], "update app.assumptions set status = 'confirmed'")
    with pytest.raises(psycopg.Error) as back:
        await run_as(api_db, world["expert"], "update app.assumptions set status = 'suggested'")
    assert back.value.sqlstate == "AV409"


async def test_a_suggestion_is_decided_once_and_keeps_what_it_said(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(INSERTS["prefill_suggestions"], params(world))
    with pytest.raises(psycopg.Error) as changed:
        await run_as(api_db, world["expert"], "update app.prefill_suggestions set field = 'court_track'")
    assert changed.value.sqlstate == "AV409"
    assistant = await run_as(
        api_db, world["assistant"], "update app.prefill_suggestions set status = 'dismissed' returning decided_by"
    )
    assert assistant == [(world["assistant"],)]
    with pytest.raises(psycopg.Error) as twice:
        await run_as(api_db, world["expert"], "update app.prefill_suggestions set status = 'accepted'")
    assert twice.value.sqlstate == "AV409"
    accept = "update app.prefill_suggestions set status = 'accepted' returning id"
    viewer = await run_as(api_db, world["viewer"], accept)
    assert viewer == []  # a viewer's update matches no row


async def test_answer_history_is_seen_like_the_answers_and_never_changed(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    await run_as(api_db, world["expert"], INSERTS["conflict_answers"], params(world))
    await run_as(api_db, world["expert"], "update app.conflict_answers set answer = 'yes'")
    count = "select count(*) from app.conflict_answer_history"
    assert await run_as(api_db, world["viewer"], count) == [(2,)]
    assert await run_as(api_db, world["outsider"], count) == [(0,)]
    for sql in (
        "update app.conflict_answer_history set answer = 'no'",
        "delete from app.conflict_answer_history",
        "insert into app.conflict_answer_history (case_id, question_key, answer)"
        f" values ('{world['case']}', 'k', 'no')",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await run_as(api_db, world["expert"], sql)
