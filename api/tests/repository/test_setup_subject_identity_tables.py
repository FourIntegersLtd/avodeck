"""The subject's own name, other names and date of birth (slice C, migration 0396; ADR on sealed identity).

Sealed bytes only; the named expert writes and reads (rule 9, row-level security repeats the check); other
case members learn only whether an identity was recorded; the worker, inside a job for the case, can read.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

INSERT = (
    "insert into app.case_subject_identity (case_id, name, other_names, date_of_birth)"
    " values (%(c)s, '\\x01', '\\x02', '\\x03')"
)


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant, viewer, outsider = (seed.person() for _ in range(4))
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant", viewer: "expert"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    return {"expert": expert, "assistant": assistant, "viewer": viewer, "outsider": outsider, "case": case}


def test_the_columns_are_sealed_bytes(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "select column_name, data_type, is_nullable from information_schema.columns"
        " where table_schema = 'app' and table_name = 'case_subject_identity' order by column_name"
    ).fetchall()
    assert rows == [
        ("case_id", "uuid", "NO"),
        ("date_of_birth", "bytea", "YES"),
        ("name", "bytea", "NO"),
        ("other_names", "bytea", "YES"),
        ("updated_at", "timestamp with time zone", "NO"),
    ]


async def test_the_named_expert_writes_and_reads(api_db: Database, world: dict[str, uuid.UUID]) -> None:
    async with api_db.request(scope_for(world["expert"])) as conn:
        await conn.execute(INSERT, {"c": world["case"]})
        await conn.execute("update app.case_subject_identity set name = '\\x09' where case_id = %s", (world["case"],))
        row = await (await conn.execute("select name from app.case_subject_identity")).fetchone()
    assert row is not None and bytes(row[0]) == b"\x09"


@pytest.mark.parametrize("who", ["assistant", "viewer", "outsider"])
async def test_nobody_else_writes_or_reads_the_sealed_values(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID], who: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with api_db.request(scope_for(world[who])) as conn:
            await conn.execute(INSERT, {"c": world["case"]})
    admin.execute(INSERT, {"c": world["case"]})
    async with api_db.request(scope_for(world[who])) as conn:
        row = await (await conn.execute("select count(*) from app.case_subject_identity")).fetchone()
        updated = await conn.execute(
            "update app.case_subject_identity set name = '\\x09' where case_id = %s", (world["case"],)
        )
    assert row == (0,) and updated.rowcount == 0


async def test_case_members_learn_only_whether_it_is_recorded(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    async def recorded(who: str) -> bool | None:
        async with api_db.request(scope_for(world[who])) as conn:
            row = await (await conn.execute("select app.case_identity_recorded(%s)", (world["case"],))).fetchone()
        assert row is not None
        return row[0]

    assert await recorded("viewer") is False
    admin.execute(INSERT, {"c": world["case"]})
    assert await recorded("viewer") is True
    assert await recorded("assistant") is True
    assert await recorded("outsider") is False


def test_the_worker_reads_inside_a_job_for_the_case_only(
    worker_dsn: str, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(INSERT, {"c": world["case"]})
    with psycopg.connect(worker_dsn) as conn:
        assert conn.execute("select count(*) from app.case_subject_identity").fetchone() == (0,)
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(world["case"]),))
        row = conn.execute("select name from app.case_subject_identity").fetchone()
        assert row is not None and bytes(row[0]) == b"\x01"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update app.case_subject_identity set name = '\\x09'")
