"""Slice A's tables (migrations 0100+): who can see and write them (rule 9, ADR 0013).

The API sees a case's rows only with access to the case; the worker only inside a job on that case.
Neither can see another case's rows. A down migration exists for every up migration.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.packs import REPO
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

MIGRATIONS = REPO / "supabase" / "migrations"


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def slice_migrations() -> list[str]:
    return sorted(p.name for p in MIGRATIONS.glob("01??_*.sql"))


def test_every_slice_migration_is_in_range_and_has_a_down_file() -> None:
    names = slice_migrations()
    assert names, "slice A has no migrations"
    for name in names:
        assert re.match(r"^01\d\d_pipeline_[a-z0-9_]+\.sql$", name), name
        assert (MIGRATIONS / "down" / name).is_file(), f"no down migration for {name}"


def two_cases(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    expert, stranger = seed.person(), seed.person()
    mine = seed.case(seed.org(members={expert: "owner"}), expert)
    theirs = seed.case(seed.org(members={stranger: "owner"}), stranger)
    return expert, stranger, mine, theirs


async def test_document_scans_are_seen_with_case_access_or_in_the_cases_own_job(
    admin: psycopg.Connection, seed: Seed, api_db: Database, worker_db: Database
) -> None:
    expert, stranger, mine, theirs = two_cases(seed)
    doc = seed.document(mine)
    other = seed.document(theirs)
    async with worker_db.job(mine) as conn:
        await conn.execute(
            "insert into app.document_scans (document_id, bundle_id, clean)"
            " values (%s, (select bundle_id from app.cases where id = %s), true)",
            (doc, mine),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():  # a savepoint, so the refusal does not undo the row above
                await conn.execute(
                    "insert into app.document_scans (document_id, bundle_id, clean)"
                    " values (%s, (select bundle_id from app.cases where id = %s), true)",
                    (other, theirs),
                )
    async with worker_db.job(theirs) as conn:
        cur = await conn.execute(
            "select count(*) from app.document_scans"
            " where bundle_id = (select bundle_id from app.cases where id = %s)",
            (mine,),
        )
        assert await cur.fetchone() == (0,)
    async with api_db.request(scope_for(expert)) as conn:
        cur = await conn.execute(
            "select document_id from app.document_scans"
            " where bundle_id = (select bundle_id from app.cases where id = %s)",
            (mine,),
        )
        assert await cur.fetchall() == [(doc,)]
    async with api_db.request(scope_for(stranger)) as conn:
        cur = await conn.execute(
            "select count(*) from app.document_scans"
            " where bundle_id = (select bundle_id from app.cases where id = %s)",
            (mine,),
        )
        assert await cur.fetchone() == (0,)


async def test_page_analysis_rows_belong_to_a_page_of_the_same_case(
    admin: psycopg.Connection, seed: Seed, worker_db: Database
) -> None:
    _, _, mine, theirs = two_cases(seed)
    page = seed.page(theirs, seed.document(theirs), 1)
    with pytest.raises(psycopg.errors.CheckViolation):
        async with worker_db.job(mine) as conn:
            await conn.execute(
                "insert into app.page_analysis (page_id, bundle_id)"
                " values (%s, (select bundle_id from app.cases where id = %s))",
                (page, mine),
            )
