"""A paused job's back-off row sits on the queue its kind runs on; handing the job back finds it there.

Since the interactive/bulk split a paused job waits on `bulk` or `interactive`, but the hand-back callers
(`app.resume_paused_job()`, `app.support_job_action()`) name the old `jobs` queue. The waiting row must be found by
the job alone and brought forward on its own queue: never a second waiting row, so the job never runs twice.
A support requeue of a failed interactive kind (export_case) is deferred on `interactive`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core import jobs
from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext
from app.features.close.jobs import register as register_close
from tests.repository.test_foundation_support_job_actions import run_until
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

QUEUES = ["bulk", "interactive", "jobs"]


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def paused_job_waiting_on(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, queue: str, kind: str = "zz_paused"
) -> uuid.UUID:
    """A job paused by a resumable pause: its back-off row waits, an hour away, on `queue`."""
    job = seed.job(case, kind=kind)
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'paused', error_code = 'service_busy' where id = %s", (job,))
    admin.execute("set search_path = procrastinate, public")  # the queue's triggers use bare type names
    admin.execute(
        "select procrastinate_defer_jobs_v1(array[row(%s, 'run_job', 0, %s, null, jsonb_build_object('job_id', %s::text),"
        " null)::procrastinate_job_to_defer_v1])",
        (queue, f"case:{case}", str(job)),
    )
    admin.execute(
        "update procrastinate.procrastinate_jobs set scheduled_at = now() + interval '1 hour' where args ->> 'job_id' = %s",
        (str(job),),
    )
    admin.execute("reset search_path")
    return job


def queue_rows(admin: psycopg.Connection, job: uuid.UUID) -> list[tuple[str, str, bool]]:
    """(queue, status, due now) of every queue row the job has."""
    return admin.execute(
        "select queue_name, status::text, coalesce(scheduled_at, now()) <= now()"
        " from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s order by id",
        (str(job),),
    ).fetchall()


@pytest.fixture
def people(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    expert, staff = seed.person(), seed.person()
    seed.support_staff(staff)
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    return expert, staff, case


@pytest.mark.parametrize("queue", QUEUES)
async def test_try_again_brings_the_waiting_row_forward_on_its_own_queue(
    queue: str, api_db: Database, admin: psycopg.Connection, seed: Seed, people: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, _, case = people
    job = paused_job_waiting_on(admin, seed, case, queue)
    async with api_db.request(scope_for(expert)) as conn:
        cur = await conn.execute("select app.resume_paused_job(%s, 'jobs')", (job,))
        assert await cur.fetchone() == (True,)
    assert queue_rows(admin, job) == [(queue, "todo", True)], "one waiting row, on the queue it was on, due now"


@pytest.mark.parametrize("queue", QUEUES)
async def test_support_resume_brings_the_waiting_row_forward_on_its_own_queue(
    queue: str, api_db: Database, admin: psycopg.Connection, seed: Seed, people: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    _, staff, case = people
    job = paused_job_waiting_on(admin, seed, case, queue)
    async with api_db.request(scope_for(staff)) as conn:
        done = await jobs.support_job_action(conn, job, "resume", "service_restored", queue="jobs")
    assert done.outcome == "ok"
    assert queue_rows(admin, job) == [(queue, "todo", True)]


@pytest.mark.parametrize("queue", ["bulk", "interactive", "jobs"])
async def test_try_again_accepts_every_queue_the_registry_knows(
    queue: str, api_db: Database, admin: psycopg.Connection, seed: Seed, people: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, _, case = people
    job = seed.job(case, kind="zz_paused")
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'paused', error_code = 'cost_guard' where id = %s", (job,))
    async with api_db.request(scope_for(expert)) as conn:
        cur = await conn.execute("select app.resume_paused_job(%s, %s)", (job, queue))
        assert await cur.fetchone() == (True,)
    assert [(q, s) for q, s, _ in queue_rows(admin, job)] == [(queue, "todo")], "no row to move: a new one on `queue`"


async def test_a_paused_job_on_the_bulk_queue_runs_once_after_support_resumes_it(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    expert, staff, case = people
    registry = isolated_registry(split_queues=True)
    runs: list[int] = []

    @registry.kind("zz_busy_once", changes_case=False)
    async def busy_once(ctx: JobContext) -> None:
        runs.append(ctx.attempt)
        if len(runs) == 1:
            raise ProcessingPaused("service_busy")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_busy_once")
    async with open_worker(
        registry, worker_db, worker_dsn, queues=[registry.bulk_queue], pause_delays=(3600.0,)
    ) as worker:
        assert (await run_until_finished(worker, admin, [job]))[job] == "paused"
        assert [(q, s) for q, s, _ in queue_rows(admin, job)] == [(registry.bulk_queue, "todo")]
        async with api_db.request(scope_for(staff)) as conn:
            done = await jobs.support_job_action(conn, job, "resume", "service_restored", queue=registry.queue)
        assert done.outcome == "ok"
        await run_until(worker, admin, job, "done")
    assert len(runs) == 2, "the job ran once more, not twice"
    assert [(q, s) for q, s, _ in queue_rows(admin, job)] == [(registry.bulk_queue, "succeeded")]


async def test_a_support_requeue_of_a_failed_interactive_kind_goes_to_the_interactive_queue(
    api_db: Database, admin: psycopg.Connection, seed: Seed, monkeypatch: pytest.MonkeyPatch,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    _, staff, case = people
    registry = isolated_registry(split_queues=True)
    register_close(registry)
    monkeypatch.setattr(jobs, "registry", registry)
    job = seed.job(case, kind="export_case")
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'failed', error_code = 'job_error' where id = %s", (job,))
    async with api_db.request(scope_for(staff)) as conn:
        done = await jobs.support_job_action(conn, job, "requeue", "fault_fixed")
    assert (done.outcome, done.status) == ("ok", "queued")
    assert [(q, s) for q, s, _ in queue_rows(admin, job)] == [(registry.interactive_queue, "todo")]
