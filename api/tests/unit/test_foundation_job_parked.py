"""A handler can park its job (page-batch jobs, ADR 0038, F55-F60).

`JobParked` leaves the job `running` and ends the queue row: no `_finish`, no `done`, no dead letter, no retry.
The slot and the case lock are free; something else (a child's last write, the heal sweep) wakes the job later with
a new queue row for the same job.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from psycopg import AsyncConnection

from app.core import jobs
from app.core.db import Database
from app.core.jobs import JobContext, JobParked
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker
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


def queue_status(admin: psycopg.Connection, job: uuid.UUID) -> str | None:
    found = admin.execute(
        "select status::text from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s order by id desc",
        (str(job),),
    ).fetchone()
    return None if found is None else str(found[0])


async def run_until_queue_row_ends(worker: jobs.JobWorker, admin: psycopg.Connection, job: uuid.UUID) -> None:
    task = asyncio.create_task(worker.run())
    deadline = time.monotonic() + 30
    try:
        while queue_status(admin, job) not in ("succeeded", "failed"):
            assert time.monotonic() < deadline, "the queue row did not end"
            assert not task.done()
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _noop(conn: AsyncConnection) -> None:
    return None


async def test_a_parked_job_stays_running_and_its_queue_row_ends(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead: list[str] = []
    monkeypatch.setattr(jobs, "_dead_letter", lambda kind, job: dead.append(kind))
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()
    runs: list[int] = []

    @registry.kind("zz_parks", changes_case=True)
    async def parks(ctx: JobContext) -> None:
        runs.append(ctx.attempt)
        await ctx.step("planned", _noop)
        raise JobParked

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_parks")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_queue_row_ends(worker, admin, job)
    row = admin.execute(
        "select status, error_code, attempts, finished_at from app.jobs where id = %s", (job,)
    ).fetchone()
    assert row == ("running", None, 1, None)
    assert queue_status(admin, job) == "succeeded", "the queue row ended: the slot and the case lock are free"
    assert runs == [1], "it was not retried"
    assert dead == [], "parking is not a failure"
    assert admin.execute("select count(*) from app.job_steps where job_id = %s", (job,)).fetchone() == (1,)


async def test_a_parked_job_woken_again_does_not_count_an_attempt(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    """The wake is a new queue row for the existing job marked `requeued_stall` (ADR 0038); the worker's start
    adds no attempt for it, even to a job that is already `running`."""
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()
    runs: list[int] = []

    @registry.kind("zz_parks_twice", changes_case=True)
    async def parks_twice(ctx: JobContext) -> None:
        runs.append(ctx.attempt)
        if len(runs) == 1:
            raise JobParked

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_parks_twice")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_queue_row_ends(worker, admin, job)
        assert admin.execute("select status from app.jobs where id = %s", (job,)).fetchone() == ("running",)
        admin.execute("update app.jobs set requeued_stall = true where id = %s", (job,))
        admin.execute("set search_path = procrastinate, public")
        admin.execute(
            "select procrastinate_defer_jobs_v1(array[row(%s, 'run_job', 0, %s, null,"
            " jsonb_build_object('job_id', %s::text, 'case_id', %s::text), null)::procrastinate_job_to_defer_v1])",
            (registry.queue, f"case:{case}", str(job), str(case)),
        )
        admin.execute("reset search_path")
        task = asyncio.create_task(worker.run())
        deadline = time.monotonic() + 30
        try:
            while admin.execute("select status from app.jobs where id = %s", (job,)).fetchone() != ("done",):
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    assert runs == [1, 1], "the wake ran the handler again with the same attempt number"
    assert admin.execute("select attempts from app.jobs where id = %s", (job,)).fetchone() == (1,)
