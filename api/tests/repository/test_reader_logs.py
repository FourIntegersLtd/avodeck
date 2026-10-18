"""The read log (`page_reads`), the verification log (`fact_verifications`) and fact flags (`fact_flags`).

- Each read appends one `page_reads` row hashed onto the case's previous read; the chain verifies and
  altering any row breaks it. Neither log can be changed or removed by the running app or by hand
  (only `app.purge_case` removes a deleted case's rows).
- A second read keeps the first `read_at` and adds its own dwell.
- Only the named expert's reads count for citing; an assistant's reads are recorded (and unlock the
  suggestions for the assistant) but never make a fact citable (permissions `page.read`).
- Nobody records a read for someone else, and a person with no access to the case records nothing.
- Slice H's certificate carries the head of each chain (`app.close_log_chain_heads`).
- The app's own `create_app()` uses this log for the blind-read gate (rule 3).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.features.reader import repo
from app.features.reader.log import ReaderPageReadLog
from tests.support.contracts import add_fact
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ZERO = "0" * 64


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=1)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert, assistant, stranger = seed.person(), seed.person(), seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    doc = seed.document(case)
    pages = {n: seed.page(case, doc, n) for n in (1, 2)}
    return {"case": case, "expert": expert, "assistant": assistant, "stranger": stranger, "pages": pages}


async def read(api_db: Database, w: dict[str, Any], person: uuid.UUID, n: int, dwell: int = 30) -> None:
    async with api_db.request(scope_for(person)) as conn:
        await repo.record_read(conn, w["case"], w["pages"][n], person, dwell)


def reads_of(admin: psycopg.Connection, case: uuid.UUID) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select seq, page_id, person_id, read_at, dwell_seconds, counts_for_citing, prev_hash, hash"
        " from app.page_reads where case_id = %s order by seq",
        (case,),
    ).fetchall()


def chain_ok(admin: psycopg.Connection, log: str, case: uuid.UUID) -> bool:
    row = admin.execute(f"select app.{log}_chain_ok(%s)", (case,)).fetchone()  # noqa: S608
    assert row is not None
    return bool(row[0])


def tamper(admin: psycopg.Connection, sql: str, args: tuple[Any, ...]) -> None:
    """Change a row behind the triggers' back, as someone with the database itself would."""
    admin.execute("set session_replication_role = replica")
    try:
        admin.execute(sql, args)
    finally:
        admin.execute("set session_replication_role = origin")


async def test_each_read_appends_a_row_hashed_onto_the_last(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    await read(api_db, world, world["expert"], 1)
    await read(api_db, world, world["expert"], 2)
    rows = reads_of(admin, world["case"])
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][6] == ZERO and rows[1][6] == rows[0][7]
    assert all(len(r[7]) == 64 and r[7] != ZERO for r in rows)
    assert chain_ok(admin, "page_reads", world["case"])


async def test_altering_any_read_breaks_the_chain(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    for n in (1, 2, 1):
        await read(api_db, world, world["expert"], n)
    tamper(admin, "update app.page_reads set dwell_seconds = 999 where case_id = %s and seq = 2", (world["case"],))
    assert not chain_ok(admin, "page_reads", world["case"])


async def test_removing_a_read_breaks_the_chain(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    for n in (1, 2, 1):
        await read(api_db, world, world["expert"], n)
    tamper(admin, "delete from app.page_reads where case_id = %s and seq = 2", (world["case"],))
    assert not chain_ok(admin, "page_reads", world["case"])


async def test_reads_cannot_be_changed_or_removed(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    await read(api_db, world, world["expert"], 1)
    for sql in ("update app.page_reads set dwell_seconds = 1", "delete from app.page_reads"):
        async with api_db.request(scope_for(world["expert"])) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(sql + " where case_id = %s", (world["case"],))
        with pytest.raises(psycopg.Error) as refused:
            admin.execute(sql + " where case_id = %s", (world["case"],))
        assert refused.value.sqlstate == "AV409"
    assert len(reads_of(admin, world["case"])) == 1


async def test_a_second_read_keeps_the_first_and_adds_its_dwell(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    await read(api_db, world, world["expert"], 1, dwell=40)
    first = reads_of(admin, world["case"])[0]
    await read(api_db, world, world["expert"], 1, dwell=15)
    rows = reads_of(admin, world["case"])
    assert rows[0] == first
    assert [r[4] for r in rows] == [40, 15]
    assert rows[1][3] >= rows[0][3]


async def test_only_the_named_experts_reads_count_for_citing(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    await read(api_db, world, world["assistant"], 1)
    await read(api_db, world, world["expert"], 2)
    assert [(r[1], r[5]) for r in reads_of(admin, world["case"])] == [
        (world["pages"][1], False),
        (world["pages"][2], True),
    ]
    log = ReaderPageReadLog()
    case = world["case"]
    async with api_db.request(scope_for(world["assistant"])) as conn:
        assert await log.has_read(conn, case, 1, world["assistant"])
        assert not await log.has_read(conn, case, 2, world["assistant"])
        assert await log.read_page_ids(conn, case, world["assistant"]) == set()
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert not await log.has_read(conn, case, 1, world["expert"])
        assert await log.has_read(conn, case, 2, world["expert"])
        assert await log.read_page_ids(conn, case, world["expert"]) == {world["pages"][2]}


async def test_nobody_records_a_read_for_someone_else_or_without_access(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    async with api_db.request(scope_for(world["assistant"])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await repo.record_read(conn, world["case"], world["pages"][1], world["expert"], 10)
    async with api_db.request(scope_for(world["stranger"])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await repo.record_read(conn, world["case"], world["pages"][1], world["stranger"], 10)
    assert reads_of(admin, world["case"]) == []


def add_verification(admin: psycopg.Connection, w: dict[str, Any], fact: uuid.UUID, action: str) -> None:
    admin.execute(
        "insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value,"
        " old_word_ids, new_word_ids) values (%s, %s, %s, %s, 'a', 'b', array['w1'], array['w2'])",
        (w["case"], fact, w["expert"], action),
    )


def test_verifications_are_chained_and_cannot_be_changed_or_removed(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    fact = add_fact(admin, world["case"], world["pages"][1])
    add_verification(admin, world, fact, "edit")
    add_verification(admin, world, fact, "correct")
    rows = admin.execute(
        "select seq, prev_hash, hash from app.fact_verifications where case_id = %s order by seq", (world["case"],)
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2] and rows[0][1] == ZERO and rows[1][1] == rows[0][2]
    assert chain_ok(admin, "fact_verifications", world["case"])
    for sql in ("update app.fact_verifications set reason = 'x'", "delete from app.fact_verifications"):
        with pytest.raises(psycopg.Error) as refused:
            admin.execute(sql + " where case_id = %s", (world["case"],))
        assert refused.value.sqlstate == "AV409"
    tamper(admin, "update app.fact_verifications set new_value = 'c' where case_id = %s and seq = 1", (world["case"],))
    assert not chain_ok(admin, "fact_verifications", world["case"])


async def test_the_certificate_carries_the_head_of_each_real_chain(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    await read(api_db, world, world["expert"], 1)
    await read(api_db, world, world["expert"], 2)
    fact = add_fact(admin, world["case"], world["pages"][1])
    add_verification(admin, world, fact, "correct")
    add_verification(admin, world, fact, "not_right")
    heads = {
        log: admin.execute(
            f"select hash from app.{log} where case_id = %s order by seq desc limit 1",  # noqa: S608
            (world["case"],),
        ).fetchone()[0]  # type: ignore[index]
        for log in ("page_reads", "fact_verifications")
    }
    async with worker_db.job(world["case"]) as conn:
        cur = await conn.execute("select app.close_log_chain_heads(%s)", (world["case"],))
        assert (await cur.fetchone())[0] == heads  # type: ignore[index]


async def test_only_the_named_expert_flags_a_fact(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    fact = add_fact(admin, world["case"], world["pages"][1], status="verified")
    insert = "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, 'points', %s)"
    async with api_db.request(scope_for(world["assistant"])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(insert, (world["case"], fact, world["assistant"]))
    async with api_db.request(scope_for(world["expert"])) as conn:
        await conn.execute(insert, (world["case"], fact, world["expert"]))
    assert admin.execute("select count(*) from app.fact_flags where fact_id = %s", (fact,)).fetchone() == (1,)


def test_the_app_uses_the_reader_log_for_the_blind_read_gate() -> None:
    from app.main import create_app

    assert isinstance(create_app().state.page_reads, ReaderPageReadLog)
    assert create_app(page_reads=None).state.page_reads is None
