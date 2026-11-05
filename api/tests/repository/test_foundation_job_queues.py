"""Interactive and bulk jobs on separate queues, against the real queue (scalability PR 7)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.jobs import JobContext
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=10)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def queue_of(admin: psycopg.Connection, job: uuid.UUID) -> str:
    row = admin.execute(
        "select queue_name from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(job),)
    ).fetchone()
    assert row is not None
    return str(row[0])


async def _noop(ctx: JobContext) -> None:
    return None


async def test_jobs_are_queued_on_the_queue_their_kind_belongs_to(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert)
    registry = isolated_registry(split_queues=True)
    registry.kind("zz_fast", interactive=True, changes_case=False)(_noop)
    registry.kind("zz_slow", changes_case=False)(_noop)
    async with api_db.request(scope_for(expert)) as conn:
        fast = await registry.enqueue(conn, case, "zz_fast")
        slow = await registry.enqueue(conn, case, "zz_slow")
    assert queue_of(admin, fast) == registry.queue_for("zz_fast") == f"{registry.queue_prefix}interactive"
    assert queue_of(admin, slow) == registry.queue_for("zz_slow") == f"{registry.queue_prefix}bulk"


async def test_a_worker_for_the_interactive_queue_never_takes_a_bulk_job(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert)
    registry = isolated_registry(split_queues=True)
    ran: list[str] = []

    @registry.kind("zz_fast", interactive=True, changes_case=False)
    async def fast(ctx: JobContext) -> None:
        ran.append("fast")

    @registry.kind("zz_slow", changes_case=False)
    async def slow(ctx: JobContext) -> None:
        ran.append("slow")

    async with api_db.request(scope_for(expert)) as conn:
        fast_job = await registry.enqueue(conn, case, "zz_fast")
        slow_job = await registry.enqueue(conn, case, "zz_slow")
    async with open_worker(registry, worker_db, worker_dsn, queues=["interactive"]) as worker:
        await run_until_finished(worker, admin, [fast_job])
        await asyncio.sleep(0.5)
    assert ran == ["fast"]
    row = admin.execute("select status from app.jobs where id = %s", (slow_job,)).fetchone()
    assert row == ("queued",)


async def test_an_interactive_job_starts_within_a_second_while_four_bulk_jobs_run(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    cases = [seed.case(org, expert) for _ in range(5)]
    registry = isolated_registry(split_queues=True)
    started: dict[str, float] = {}

    @registry.kind("zz_slow")
    async def slow(ctx: JobContext) -> None:
        started[f"slow{len(started)}"] = time.monotonic()
        await asyncio.sleep(3)

    @registry.kind("zz_fast", interactive=True, changes_case=False)
    async def fast(ctx: JobContext) -> None:
        started["fast"] = time.monotonic()

    async with api_db.request(scope_for(expert)) as conn:
        slow_jobs = [await registry.enqueue(conn, c, "zz_slow") for c in cases[:4]]
    async with (
        open_worker(registry, worker_db, worker_dsn, queues=["bulk"]) as bulk,
        open_worker(registry, worker_db, worker_dsn, queues=["interactive"]) as interactive,
    ):
        bulk_task = asyncio.create_task(run_until_finished(bulk, admin, slow_jobs, concurrency=4))
        try:
            while len([k for k in started if k.startswith("slow")]) < 4:
                await asyncio.sleep(0.05)
            async with api_db.request(scope_for(expert)) as conn:
                fast_job = await registry.enqueue(conn, cases[4], "zz_fast")
            asked = time.monotonic()
            await run_until_finished(interactive, admin, [fast_job], concurrency=2)
            assert started["fast"] - asked < 1.0
            assert set((await bulk_task).values()) == {"done"}
        finally:
            if not bulk_task.done():
                bulk_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bulk_task


async def test_a_worker_with_no_queue_list_still_takes_a_job_queued_on_the_old_queue(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert)
    registry = isolated_registry(split_queues=True)
    ran: list[str] = []

    @registry.kind("zz_old")
    async def old(ctx: JobContext) -> None:
        ran.append("old")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_old")
    # Rollout: this job was queued by the old code, on the single legacy queue.
    admin.execute(
        "update procrastinate.procrastinate_jobs set queue_name = %s where args ->> 'job_id' = %s",
        (registry.queue, str(job)),
    )
    admin.commit()
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert registry.queue in worker.queues
        await run_until_finished(worker, admin, [job])
    assert ran == ["old"]
