"""Failure modes F6 and F28 (docs/spec/failure-modes.md), owned by the foundation.

F6: a worker crashes mid-job. Its heartbeat stops, the sweep puts the job back on the queue, and
the next worker carries on: steps that finished are skipped, so nothing is done twice.

F28: a scheduled job is missed (deploy, outage). Runs are keyed by date; the next run catches up
each missed date exactly once, so reminders arrive late but never twice.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta

import psycopg
import pytest
from psycopg import AsyncConnection

from app.core.db import Database
from app.core.jobs import JobContext
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
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


def procrastinate_admin(admin_dsn: str) -> psycopg.Connection:
    return psycopg.connect(admin_dsn, autocommit=True, options="-c search_path=procrastinate")


async def test_f6_a_crashed_workers_job_is_requeued_and_skips_the_steps_it_finished(
    api_db: Database, worker_db: Database, worker_dsn: str, admin_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()
    done: list[str] = []

    @registry.kind("zz_two_steps")
    async def two_steps(ctx: JobContext) -> None:
        async def first(conn: AsyncConnection) -> None:
            done.append("first")

        async def second(conn: AsyncConnection) -> None:
            done.append("second")

        await ctx.step("first", first)
        await ctx.step("second", second)

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_two_steps")

    # A worker takes the job, finishes the first step and dies: its heartbeat goes stale.
    with procrastinate_admin(admin_dsn) as q:
        dead = q.execute("select procrastinate_register_worker_v1()").fetchone()[0]  # type: ignore[index]
        taken = q.execute("select id from procrastinate_fetch_job_v2(%s, %s)", ([registry.queue], dead)).fetchone()
        assert taken is not None and taken[0] is not None
        q.execute("update procrastinate_workers set last_heartbeat = now() - interval '1 hour' where id = %s", (dead,))
    admin.execute("update app.jobs set status = 'running', attempts = 1, started_at = now() where id = %s", (job,))
    admin.execute("insert into app.job_steps (job_id, case_id, step) values (%s, %s, 'first')", (job, case))

    async with open_worker(registry, worker_db, worker_dsn, stalled_after_seconds=60) as worker:
        assert await worker.requeue_stalled() == 1
        await run_until_finished(worker, admin, [job])

    assert done == ["second"], "a finished step ran again"
    row = admin.execute("select status, attempts, pct from app.jobs where id = %s", (job,)).fetchone()
    assert row == ("done", 2, 100)
    steps = admin.execute("select step from app.job_steps where job_id = %s order by step", (job,)).fetchall()
    assert steps == [("first",), ("second",)]


async def test_f6_a_live_workers_job_is_left_alone(
    api_db: Database, worker_db: Database, worker_dsn: str, admin_dsn: str, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()

    @registry.kind("zz_busy")
    async def busy(ctx: JobContext) -> None:  # pragma: no cover - never runs here
        raise AssertionError

    async with api_db.request(scope_for(expert)) as conn:
        await registry.enqueue(conn, case, "zz_busy")
    with procrastinate_admin(admin_dsn) as q:
        alive = q.execute("select procrastinate_register_worker_v1()").fetchone()[0]  # type: ignore[index]
        q.execute("select id from procrastinate_fetch_job_v2(%s, %s)", ([registry.queue], alive))
        try:
            async with open_worker(registry, worker_db, worker_dsn, stalled_after_seconds=60) as worker:
                assert await worker.requeue_stalled() == 0
        finally:
            q.execute("update procrastinate_jobs set status = 'cancelled' where queue_name = %s", (registry.queue,))


async def test_f28_a_missed_daily_run_catches_up_each_missed_date_exactly_once(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    registry = isolated_registry()
    name = f"zz_daily_{uuid.uuid4().hex[:8]}"
    ran: list[date] = []

    @registry.schedule(name)
    async def daily(conn: AsyncConnection, run_date: date) -> None:
        ran.append(run_date)

    today = date(2026, 10, 17)
    admin.execute("insert into app.scheduled_runs (name, run_date) values (%s, %s)", (name, today - timedelta(days=3)))

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        first = await worker.catch_up(name, today)
        again = await worker.catch_up(name, today)
        tomorrow = await worker.catch_up(name, today + timedelta(days=1))

    expected = [today - timedelta(days=2), today - timedelta(days=1), today]
    assert first == expected
    assert again == []
    assert tomorrow == [today + timedelta(days=1)]
    assert ran == [*expected, today + timedelta(days=1)]


async def test_f28_a_schedule_that_never_ran_runs_today_only_and_a_failed_date_is_tried_again(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    registry = isolated_registry()
    name = f"zz_daily_{uuid.uuid4().hex[:8]}"
    attempts: list[date] = []
    fail = {date(2026, 10, 18)}

    @registry.schedule(name)
    async def daily(conn: AsyncConnection, run_date: date) -> None:
        attempts.append(run_date)
        if run_date in fail:
            raise RuntimeError("synthetic outage")

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert await worker.catch_up(name, date(2026, 10, 17)) == [date(2026, 10, 17)]
        with pytest.raises(RuntimeError):
            await worker.catch_up(name, date(2026, 10, 18))
        fail.clear()
        assert await worker.catch_up(name, date(2026, 10, 18)) == [date(2026, 10, 18)]

    assert attempts == [date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 18)]
    runs = admin.execute(
        "select run_date from app.scheduled_runs where name = %s order by run_date", (name,)
    ).fetchall()
    assert runs == [(date(2026, 10, 17),), (date(2026, 10, 18),)]
