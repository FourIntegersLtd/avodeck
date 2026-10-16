"""audit_log: every row records person, organisation, case, action and target; rows are
append-only for the running app and hash-chained, so tampering is detected (architecture 10.1, 10.9)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from app.core import audit
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

ROW_SQL = (
    "select seq, person_id, organisation_id, case_id, action, target, at, prev_hash, hash"
    " from app.audit_log order by seq"
)


def all_rows(admin: psycopg.Connection) -> list[audit.AuditRow]:
    return [audit.AuditRow(*r) for r in admin.execute(ROW_SQL).fetchall()]


@pytest.fixture
def case_world(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    return expert, org, seed.case(org, expert)


@pytest.mark.anyio
async def test_record_writes_who_for_which_organisation_on_which_case_what_and_to_what(
    api_db: Database, case_world: tuple[uuid.UUID, uuid.UUID, uuid.UUID], admin: psycopg.Connection
) -> None:
    expert, org, case = case_world
    async with api_db.request(scope_for(expert)) as conn:
        await audit.record(conn, action="case.updated", target=f"case:{case}", case_id=case, organisation_id=org)
    row = admin.execute(
        "select person_id, organisation_id, case_id, action, target, hash from app.audit_log"
        " where case_id = %s order by seq desc limit 1",
        (case,),
    ).fetchone()
    assert row is not None
    assert row[:5] == (expert, org, case, "case.updated", f"case:{case}")
    assert len(row[5]) == 64


@pytest.mark.anyio
async def test_the_chain_verifies(
    api_db: Database, case_world: tuple[uuid.UUID, uuid.UUID, uuid.UUID], admin: psycopg.Connection
) -> None:
    expert, org, case = case_world
    async with api_db.request(scope_for(expert)) as conn:
        for i in range(3):
            await audit.record(conn, action="test.step", target=f"step:{i}", case_id=case, organisation_id=org)
    rows = all_rows(admin)
    assert len(rows) >= 3
    assert audit.find_breaks(rows) == []


@pytest.fixture
def tampered(admin: psycopg.Connection, case_world: tuple[uuid.UUID, uuid.UUID, uuid.UUID]) -> Iterator[int]:
    expert, org, case = case_world
    admin.execute(
        "insert into app.audit_log (person_id, organisation_id, case_id, action, target)"
        " values (%s, %s, %s, 'test.tamper', 'original')",
        (expert, org, case),
    )
    (seq,) = admin.execute("select max(seq) from app.audit_log where action = 'test.tamper'").fetchone()  # type: ignore[misc]
    admin.execute("update app.audit_log set target = 'changed' where seq = %s", (seq,))
    try:
        yield int(seq)
    finally:
        admin.execute("update app.audit_log set target = 'original' where seq = %s", (seq,))


def test_tampering_with_a_row_is_detected(admin: psycopg.Connection, tampered: int) -> None:
    assert tampered in audit.find_breaks(all_rows(admin))


@pytest.mark.anyio
async def test_removing_a_row_is_detected(
    api_db: Database, case_world: tuple[uuid.UUID, uuid.UUID, uuid.UUID], admin: psycopg.Connection
) -> None:
    expert, org, case = case_world
    async with api_db.request(scope_for(expert)) as conn:
        for i in range(3):
            await audit.record(conn, action="test.step", target=f"step:{i}", case_id=case, organisation_id=org)
    rows = all_rows(admin)
    assert len(rows) >= 3
    assert audit.find_breaks(rows[:1] + rows[2:]) == [rows[2].seq]


@pytest.mark.anyio
async def test_the_running_app_cannot_change_or_delete_audit_rows(
    api_db: Database, case_world: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, org, case = case_world
    for sql in (
        "update app.audit_log set target = 'x' where case_id = %s",
        "delete from app.audit_log where case_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with api_db.request(scope_for(expert)) as conn:
                await audit.record(conn, action="test.x", target="x", case_id=case, organisation_id=org)
                await conn.execute(sql, (case,))


@pytest.mark.anyio
async def test_a_person_cannot_write_audit_rows_in_someone_elses_name(
    api_db: Database, case_world: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, org, case = case_world
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with api_db.request(scope_for(expert)) as conn:
            await conn.execute(
                "insert into app.audit_log (person_id, action, target) values (%s, 'x', 'y')", (uuid.uuid4(),)
            )
