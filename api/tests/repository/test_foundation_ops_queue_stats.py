"""Migration 0059: the API role can read queue numbers (and only numbers) through two definer functions."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core import metrics
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def test_the_api_role_sees_queue_depth_and_age_without_ids(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case_id = seed.case(org, expert)
    registry = isolated_registry()

    @registry.kind("zz_waiting")
    async def waiting(ctx: object) -> None:  # never run in this test
        return None

    async with api_db.request(scope_for(expert)) as conn:
        await registry.enqueue(conn, case_id, "zz_waiting")
        await registry.enqueue(conn, case_id, "zz_waiting")
    admin.execute(
        "update procrastinate.procrastinate_events set at = now() - interval '90 seconds'"
        " where job_id in (select id from procrastinate.procrastinate_jobs where queue_name = %s)",
        (registry.queue,),
    )
    admin.commit()

    async with api_db.anonymous() as conn:
        cur = await conn.execute("select queue_name, queued, oldest_seconds from app.ops_queue_stats()")
        rows = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
    queued, oldest = rows[registry.queue]
    assert queued == 2
    assert 85 <= oldest <= 200


async def test_a_job_scheduled_for_later_is_not_waiting(api_db: Database, admin: psycopg.Connection) -> None:
    queue = f"test_{uuid.uuid4().hex[:12]}"
    admin.execute(
        "insert into procrastinate.procrastinate_jobs (queue_name, task_name, scheduled_at)"
        " values (%s, 'zz', now() + interval '1 hour')",
        (queue,),
    )
    admin.commit()
    async with api_db.anonymous() as conn:
        cur = await conn.execute("select queue_name from app.ops_queue_stats()")
        assert queue not in {r[0] for r in await cur.fetchall()}


async def test_job_run_time_p95_is_by_kind_over_done_jobs(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case_id = seed.case(org, expert)
    for _ in range(3):
        job = seed.job(case_id)
        admin.execute(
            "update app.jobs set kind = 'zz_timed', status = 'done', started_at = now() - interval '20 seconds',"
            " finished_at = now() - interval '10 seconds' where id = %s",
            (job,),
        )
    admin.commit()
    async with api_db.anonymous() as conn:
        cur = await conn.execute("select kind, p95_seconds from app.ops_job_seconds()")
        found = {r[0]: r[1] for r in await cur.fetchall()}
    assert found["zz_timed"] == pytest.approx(10.0, abs=0.5)


async def test_collect_fills_the_snapshot_from_the_pool_and_the_queue(
    api_db: Database, admin: psycopg.Connection
) -> None:
    metrics.reset()
    queue = f"test_{uuid.uuid4().hex[:12]}"
    admin.execute("insert into procrastinate.procrastinate_jobs (queue_name, task_name) values (%s, 'zz')", (queue,))
    admin.commit()
    snap = await metrics.collect(api_db, force=True)
    assert snap["pool_size"] >= 1
    assert snap["jobs_queued"][queue] == 1
    metrics.reset()
