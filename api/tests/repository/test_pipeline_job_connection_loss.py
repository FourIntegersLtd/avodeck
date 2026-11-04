"""A job whose database connection drops mid-run does not stay `running` for ever.

The hosted pooler closes idle or old connections ("SSL connection has been closed unexpectedly"). When that
happened while the worker was recording that a job had failed, the recording itself raised, the queue row
ended `failed` and the person-visible `app.jobs` row was left `running`: the progress screen spun for ever.

Two defences: the recording is retried on a fresh connection a few times, and a sweep (at worker start and
every housekeeping round) puts back any `running` job whose queue row is no longer waiting or held, keeping
to the state machine (running -> failed -> queued while attempts remain, else running -> failed).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from psycopg import AsyncConnection

from app.core.db import Database
from app.core.jobs import JobContext
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

DROPPED = "SSL connection has been closed unexpectedly"
NO_WAIT = (0.0, 0.0, 0.0)


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def queue_admin(admin_dsn: str) -> psycopg.Connection:
    return psycopg.connect(admin_dsn, autocommit=True, options="-c search_path=procrastinate")


async def work_once(worker: Any, queue: str) -> None:
    """Runs what is waiting on the queue, then returns."""
    await worker.queue.run_worker_async(queues=[queue], wait=False, concurrency=1, listen_notify=False)


def drop_after(worker: Any, state: dict[str, Any], *, times: int) -> None:
    """Makes the worker's connection fail `times` times once the handler has failed."""
    real = worker.database.job
    state["dropped"] = 0

    @contextlib.asynccontextmanager
    async def flaky(case: uuid.UUID) -> AsyncIterator[AsyncConnection]:
        if state.get("raised") and state["dropped"] < times:
            state["dropped"] += 1
            raise psycopg.OperationalError(DROPPED)
        async with real(case) as conn:
            yield conn

    worker.database.job = flaky


async def enqueue(api_db: Database, seed: Seed, registry: Any, kind: str) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    async with api_db.request(scope_for(expert)) as conn:
        return case, await registry.enqueue(conn, case, kind)


def row(admin: psycopg.Connection, job: uuid.UUID) -> tuple[Any, ...]:
    found = admin.execute("select status, attempts, error_code from app.jobs where id = %s", (job,)).fetchone()
    assert found is not None
    return found


def queued_rows(admin: psycopg.Connection, queue: str) -> int:
    found = admin.execute(
        "select count(*) from procrastinate.procrastinate_jobs where queue_name = %s and status = 'todo'", (queue,)
    ).fetchone()
    assert found is not None
    return int(found[0])


def leave_running_with_a_finished_queue_row(
    admin: psycopg.Connection, admin_dsn: str, queue: str, job: uuid.UUID, *, attempts: int
) -> None:
    """What a dropped connection left behind: `running` in the app's table, `failed` in the queue's."""
    admin.execute(
        "update app.jobs set status = 'running', attempts = %s, started_at = now() where id = %s", (attempts, job)
    )
    with queue_admin(admin_dsn) as q:
        q.execute("update procrastinate_jobs set status = 'failed' where queue_name = %s", (queue,))


async def test_a_connection_lost_while_recording_a_failure_is_retried_on_a_new_one(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    state: dict[str, Any] = {}
    runs: list[int] = []

    @registry.kind("zz_flaky")
    async def flaky(ctx: JobContext) -> None:
        runs.append(ctx.attempt)
        if len(runs) == 1:
            state["raised"] = True
            raise RuntimeError("the handler fails once")

    _, job = await enqueue(api_db, seed, registry, "zz_flaky")
    async with open_worker(registry, worker_db, worker_dsn, persist_waits=NO_WAIT) as worker:
        drop_after(worker, state, times=1)
        await work_once(worker, registry.queue)

    assert state["dropped"] == 1
    assert row(admin, job) == ("done", 2, None)
    assert runs == [1, 2]


async def test_a_connection_that_stays_down_for_every_try_is_put_right_by_the_sweep(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    state: dict[str, Any] = {}
    runs: list[int] = []

    @registry.kind("zz_down")
    async def down(ctx: JobContext) -> None:
        runs.append(ctx.attempt)
        if len(runs) == 1:
            state["raised"] = True
            raise RuntimeError("the handler fails once")

    _, job = await enqueue(api_db, seed, registry, "zz_down")
    async with open_worker(registry, worker_db, worker_dsn, persist_waits=NO_WAIT, orphan_grace_seconds=0) as w:
        worker: Any = w
        drop_after(worker, state, times=len(NO_WAIT) + 1)  # the first try and every retry
        await work_once(worker, registry.queue)
        assert state["dropped"] == len(NO_WAIT) + 1
        assert row(admin, job)[0] == "running"  # what the production bug left behind
        assert await worker.requeue_orphaned() >= 1  # others' stale rows in a shared test database count too
        assert row(admin, job)[0] == "queued"
        await work_once(worker, registry.queue)

    assert row(admin, job) == ("done", 2, None)


async def test_the_sweep_puts_back_a_running_job_whose_queue_row_has_ended(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, admin_dsn: str, seed: Seed
) -> None:
    registry = isolated_registry()

    @registry.kind("zz_orphan")
    async def orphan(ctx: JobContext) -> None:
        return None

    _, job = await enqueue(api_db, seed, registry, "zz_orphan")
    leave_running_with_a_finished_queue_row(admin, admin_dsn, registry.queue, job, attempts=1)
    async with open_worker(registry, worker_db, worker_dsn, orphan_grace_seconds=0) as w:
        worker: Any = w
        assert await worker.requeue_orphaned() >= 1  # others' stale rows in a shared test database count too
        assert row(admin, job) == ("queued", 1, "job_error")
        await worker.requeue_orphaned()
        assert row(admin, job) == ("queued", 1, "job_error")  # put back once, not twice
        assert queued_rows(admin, registry.queue) == 1
        await work_once(worker, registry.queue)

    assert row(admin, job) == ("done", 2, None)


async def test_the_sweep_fails_a_job_that_has_used_all_its_attempts(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, admin_dsn: str, seed: Seed
) -> None:
    registry = isolated_registry()

    @registry.kind("zz_spent")
    async def spent(ctx: JobContext) -> None:  # pragma: no cover - never runs here
        raise AssertionError

    _, job = await enqueue(api_db, seed, registry, "zz_spent")
    leave_running_with_a_finished_queue_row(admin, admin_dsn, registry.queue, job, attempts=3)
    async with open_worker(registry, worker_db, worker_dsn, orphan_grace_seconds=0) as w:
        worker: Any = w
        assert await worker.requeue_orphaned() >= 1  # others' stale rows in a shared test database count too

    assert row(admin, job) == ("failed", 3, "job_error")
    assert queued_rows(admin, registry.queue) == 0


async def test_the_sweep_leaves_alone_a_running_job_a_worker_still_holds(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, admin_dsn: str, seed: Seed
) -> None:
    registry = isolated_registry()

    @registry.kind("zz_held")
    async def held(ctx: JobContext) -> None:  # pragma: no cover - never runs here
        raise AssertionError

    _, job = await enqueue(api_db, seed, registry, "zz_held")
    admin.execute("update app.jobs set status = 'running', attempts = 1, started_at = now() where id = %s", (job,))
    with queue_admin(admin_dsn) as q:
        worker_id = q.execute("select procrastinate_register_worker_v1()").fetchone()[0]  # type: ignore[index]
        q.execute("select id from procrastinate_fetch_job_v2(%s, %s)", ([registry.queue], worker_id))
        try:
            async with open_worker(registry, worker_db, worker_dsn, orphan_grace_seconds=0) as w:
                worker: Any = w
                await worker.requeue_orphaned()
        finally:
            q.execute("update procrastinate_jobs set status = 'cancelled' where queue_name = %s", (registry.queue,))

    assert row(admin, job)[0] == "running"


async def test_the_sweep_waits_out_its_grace_before_touching_a_job(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, admin_dsn: str, seed: Seed
) -> None:
    registry = isolated_registry()

    @registry.kind("zz_fresh")
    async def fresh(ctx: JobContext) -> None:  # pragma: no cover - never runs here
        raise AssertionError

    _, job = await enqueue(api_db, seed, registry, "zz_fresh")
    leave_running_with_a_finished_queue_row(admin, admin_dsn, registry.queue, job, attempts=1)
    async with open_worker(registry, worker_db, worker_dsn, orphan_grace_seconds=60) as w:
        worker: Any = w
        await worker.requeue_orphaned()

    assert row(admin, job)[0] == "running"


async def test_the_sweep_runs_when_the_worker_starts(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, admin_dsn: str, seed: Seed
) -> None:
    from tests.support.jobs import run_until_finished

    registry = isolated_registry()

    @registry.kind("zz_restart")
    async def restart(ctx: JobContext) -> None:
        return None

    _, job = await enqueue(api_db, seed, registry, "zz_restart")
    leave_running_with_a_finished_queue_row(admin, admin_dsn, registry.queue, job, attempts=1)
    async with open_worker(registry, worker_db, worker_dsn, orphan_grace_seconds=0) as worker:
        finished = await run_until_finished(worker, admin, [job], timeout=10)

    assert finished == {job: "done"}
    assert row(admin, job)[1] == 2
