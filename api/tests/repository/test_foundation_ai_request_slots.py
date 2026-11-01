"""Migration 0049: `ai_request_slots` and `app.jobs.last_granted_at`, the Bedrock slot scheduler's
storage. Worker-only, like `job_steps` (migration 0007): the API role never touches it directly."""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def test_jobs_has_a_last_granted_at_column(admin: psycopg.Connection) -> None:
    columns = {
        r[0]
        for r in admin.execute(
            "select column_name from information_schema.columns where table_schema = 'app' and table_name = 'jobs'"
        ).fetchall()
    }
    assert "last_granted_at" in columns


def test_ai_request_slots_has_the_expected_columns(admin: psycopg.Connection) -> None:
    columns = {
        r[0]
        for r in admin.execute(
            "select column_name from information_schema.columns"
            " where table_schema = 'app' and table_name = 'ai_request_slots'"
        ).fetchall()
    }
    assert {"id", "job_id", "case_id", "requested_at", "granted_at"} <= columns


async def test_app_api_cannot_see_or_write_ai_request_slots(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    case_id = seed.case(org, expert)
    job_id = seed.job(case_id)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at) values (%s, %s, now())", (job_id, case_id)
    )
    admin.commit()

    async with api_db.request(scope_for(expert)) as conn:
        cur = await conn.execute("select count(*) from app.ai_request_slots")
        assert await cur.fetchone() == (0,)
        with pytest.raises(psycopg.Error):
            await conn.execute(
                "insert into app.ai_request_slots (job_id, case_id, requested_at) values (%s, %s, now())",
                (job_id, case_id),
            )


async def test_a_job_sees_only_its_own_case_slots(worker_db: Database, admin: psycopg.Connection, seed: Seed) -> None:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    case_a = seed.case(org, expert)
    case_b = seed.case(org, expert)
    job_a = seed.job(case_a)
    job_b = seed.job(case_b)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at) values (%s, %s, now()), (%s, %s, now())",
        (job_a, case_a, job_b, case_b),
    )
    admin.commit()

    async with worker_db.job(case_a) as conn:
        cur = await conn.execute("select job_id from app.ai_request_slots")
        assert await cur.fetchall() == [(job_a,)]
