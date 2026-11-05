"""A queue row for a job that is already `done` is skipped (page-batch jobs, F60).

A parent woken twice can leave a second queue row behind. The worker ends it at once: the handler does not run,
and the job's row is not touched (no second `done`, no new `started_at`).
"""

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
from tests.support.jobs import isolated_registry, open_worker
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

COLUMNS = "select status, attempts, started_at, finished_at, updated_at from app.jobs where id = %s"


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def defer(admin: psycopg.Connection, queue: str, job: uuid.UUID, case: uuid.UUID) -> None:
    admin.execute("set search_path = procrastinate, public")
    admin.execute(
        "select procrastinate_defer_jobs_v1(array[row(%s, 'run_job', 0, %s, null,"
        " jsonb_build_object('job_id', %s::text, 'case_id', %s::text), null)::procrastinate_job_to_defer_v1])",
        (queue, f"case:{case}", str(job), str(case)),
    )
    admin.execute("reset search_path")


async def test_a_queue_row_for_a_done_job_changes_nothing(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()
    runs: list[int] = []

    @registry.kind("zz_done_already", changes_case=True)
    async def handler(ctx: JobContext) -> None:
        runs.append(ctx.attempt)

    job = seed.job(case, kind="zz_done_already")
    admin.execute("update app.jobs set status = 'running', started_at = now() where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done', pct = 100, finished_at = now() where id = %s", (job,))
    before = admin.execute(COLUMNS, (job,)).fetchone()
    defer(admin, registry.queue, job, case)
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        task = asyncio.create_task(worker.run())
        deadline = time.monotonic() + 30
        try:
            while admin.execute(
                "select status::text from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(job),)
            ).fetchone() != ("succeeded",):
                assert time.monotonic() < deadline, "the stale queue row did not end"
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    assert runs == []
    assert admin.execute(COLUMNS, (job,)).fetchone() == before
