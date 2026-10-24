"""Support's job actions through `core/jobs.py` with a real worker (state-machines.md "Job").

A paused job that support resumes runs again and finishes (paused -> running -> done); a failed job that
support requeues runs again from attempt 1 and skips the steps it had finished (F6). Only current support
staff can call `app.support_job_action()`; the worker role cannot run it at all.
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
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext, JobWorker
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


async def run_until(worker: JobWorker, admin: psycopg.Connection, job: uuid.UUID, status: str) -> None:
    task = asyncio.create_task(worker.run())
    deadline = time.monotonic() + 30
    try:
        while True:
            row = admin.execute("select status from app.jobs where id = %s", (job,)).fetchone()
            if row is not None and row[0] == status:
                return
            if task.done():
                task.result()
            assert time.monotonic() < deadline, f"job still {row}"
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.fixture
def people(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    expert, staff = seed.person(), seed.person()
    seed.support_staff(staff)
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    return expert, staff, case


async def test_a_resumed_paused_job_runs_again_and_finishes(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    expert, staff, case = people
    registry = isolated_registry()
    runs: list[int] = []

    @registry.kind("zz_pauses_once")
    async def pauses_once(ctx: JobContext) -> None:
        runs.append(ctx.attempt)
        if len(runs) == 1:
            raise ProcessingPaused("cost_guard")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_pauses_once")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_finished(worker, admin, [job])
        assert admin.execute("select status from app.jobs where id = %s", (job,)).fetchone() == ("paused",)
        async with api_db.request(scope_for(staff)) as conn:
            done = await jobs.support_job_action(conn, job, "resume", "service_restored", queue=registry.queue)
        assert (done.outcome, done.case_id, done.status) == ("ok", case, "paused")
        await run_until(worker, admin, job, "done")
    assert len(runs) == 2


async def test_a_requeued_failed_job_starts_again_from_attempt_one_and_skips_finished_steps(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    expert, staff, case = people
    registry = isolated_registry()
    done_steps: list[str] = []
    broken = [True]

    @registry.kind("zz_fails_until_fixed")
    async def fails_until_fixed(ctx: JobContext) -> None:
        async def first(conn: AsyncConnection) -> None:
            done_steps.append("first")

        await ctx.step("first", first)
        if broken[0]:
            raise RuntimeError("broken")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_fails_until_fixed")
    async with open_worker(registry, worker_db, worker_dsn, max_attempts=1) as worker:
        await run_until(worker, admin, job, "failed")
        assert admin.execute("select status, attempts from app.jobs where id = %s", (job,)).fetchone() == ("failed", 1)
        broken[0] = False
        async with api_db.request(scope_for(staff)) as conn:
            requeued = await jobs.support_job_action(conn, job, "requeue", "fault_fixed", queue=registry.queue)
        assert (requeued.outcome, requeued.status) == ("ok", "queued")
        await run_until(worker, admin, job, "done")
    assert admin.execute("select attempts from app.jobs where id = %s", (job,)).fetchone() == (1,)
    assert done_steps == ["first"], "a finished step ran again"
    kept = admin.execute(
        "select from_status, error_code, attempts from app.job_support_actions where job_id = %s", (job,)
    ).fetchall()
    assert kept == [("failed", "job_error", 1)]


async def test_only_current_staff_and_never_the_worker_may_act_on_a_job(
    api_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    expert, _, case = people
    job = seed.job(case)
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'failed' where id = %s", (job,))
    former = seed.person()
    seed.support_staff(former, removed=True)
    for person in (expert, former):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with api_db.request(scope_for(person)) as conn:
                await jobs.support_job_action(conn, job, "requeue", "fault_fixed")
    with psycopg.connect(worker_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from app.support_job_action(%s, 'requeue', 'fault_fixed', 'jobs')", (job,))
    assert admin.execute("select status from app.jobs where id = %s", (job,)).fetchone() == ("failed",)
