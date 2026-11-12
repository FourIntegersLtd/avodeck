"""Background jobs, audit 15: behaviour the fixes add beyond the probes in the other `..._background_jobs_*` files.

BG-05/06 (a queue row may start a job only from a state a worker may start it from), BG-09 (the database, not the
caller, decides a kind's queue and priority), BG-11 (a case holds at most so many queued or running jobs of one kind),
BG-12 (a run has a time limit and a timed-out job ends `failed` / `timed_out` with the case lock released) and
BG-14 (finished queue rows are pruned, and a purged case's go with it).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import psycopg
import pytest
from starlette.requests import Request

from app.core.config import JobTimeLimits, job_time_limits, queue_retention_days
from app.core.db import Database
from app.core.errors import _database_error, too_many_jobs
from app.core.features import DEFAULT_PACKAGE, load_features
from app.core.jobs import JobContext, JobKind, job_time_limit
from tests.support.background_jobs import defer, job_row, role_connection, wait_for
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

QUEUE_FULL = "AVCAP"


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=6)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@contextmanager
def committing_api(dsn: str, person: uuid.UUID) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=20)
    try:
        conn.execute("select set_config('app.person_id', %s, false)", (str(person),))
        yield conn
    finally:
        conn.close()


def owned_case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    return expert, seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)


def enqueue(conn: psycopg.Connection, case: uuid.UUID, kind: str, queue: str = "zz_q", priority: int = 0) -> Any:
    return conn.execute("select app.enqueue_job(%s, %s, %s, %s, false, 'x')", (case, kind, queue, priority)).fetchone()


# --- BG-12: the time limit ------------------------------------------------------------------------------------------


def test_the_time_limits_default_to_two_hours_bulk_and_fifteen_minutes_interactive_and_take_the_environment() -> None:
    assert job_time_limits({}) == JobTimeLimits(bulk=7200, interactive=900)
    chosen = job_time_limits({"JOB_TIME_LIMIT_BULK_SECONDS": "60", "JOB_TIME_LIMIT_INTERACTIVE_SECONDS": "30"})
    assert chosen == JobTimeLimits(bulk=60, interactive=30)


def test_a_kind_may_set_its_own_limit_and_the_others_take_the_one_for_their_queue() -> None:
    limits = JobTimeLimits(bulk=100, interactive=10)

    def kind(**options: Any) -> JobKind:
        async def handler(ctx: JobContext) -> None:
            return None

        return JobKind("zz", handler, options.pop("interactive", False), True, **options)

    assert job_time_limit(kind(), limits) == 100
    assert job_time_limit(kind(interactive=True), limits) == 10
    assert job_time_limit(kind(time_limit_seconds=5), limits) == 5
    assert job_time_limit(kind(interactive=True, time_limit_seconds=500), limits) == 500


def test_the_queue_retention_defaults_to_thirty_days() -> None:
    assert queue_retention_days({}) == 30
    assert queue_retention_days({"QUEUE_RETENTION_DAYS": "7"}) == 7


async def test_a_handler_that_hangs_is_stopped_retried_then_failed_as_timed_out_and_frees_the_case_lock(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, api_db: Database, seed: Seed
) -> None:
    expert, case = owned_case(seed)
    registry = isolated_registry()
    attempts: list[int] = []
    later: list[uuid.UUID] = []

    @registry.kind("zz_hangs", changes_case=True)
    async def hangs(ctx: JobContext) -> None:
        attempts.append(ctx.attempt)
        await asyncio.sleep(60)

    @registry.kind("zz_after", changes_case=True)
    async def after(ctx: JobContext) -> None:
        later.append(ctx.job_id)

    async with api_db.request(scope_for(expert)) as conn:
        hung = await registry.enqueue(conn, case, "zz_hangs")
        queued_behind = await registry.enqueue(conn, case, "zz_after")
    async with open_worker(
        registry, worker_db, worker_dsn, max_attempts=2, time_limits=JobTimeLimits(bulk=0.3, interactive=0.3)
    ) as worker:
        await run_until_finished(worker, admin, [hung, queued_behind], timeout=30)
    row = admin.execute("select status, attempts, error_code from app.jobs where id = %s", (hung,)).fetchone()
    assert row == ("failed", 2, "timed_out")
    assert attempts == [1, 2], "a timeout is retried like any other failure"
    assert later == [queued_behind], "the case lock was released, so the next job on the case ran"


async def test_a_kind_with_its_own_limit_is_judged_by_it_not_by_the_workers(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, api_db: Database, seed: Seed
) -> None:
    expert, case = owned_case(seed)
    registry = isolated_registry()

    @registry.kind("zz_patient", changes_case=False, time_limit_seconds=5)
    async def patient(ctx: JobContext) -> None:
        await asyncio.sleep(0.6)

    @registry.kind("zz_impatient", changes_case=False, time_limit_seconds=0.2)
    async def impatient(ctx: JobContext) -> None:
        await asyncio.sleep(60)

    async with api_db.request(scope_for(expert)) as conn:
        slow_ok = await registry.enqueue(conn, case, "zz_patient")
        too_slow = await registry.enqueue(conn, case, "zz_impatient")
    async with open_worker(
        registry, worker_db, worker_dsn, max_attempts=1, time_limits=JobTimeLimits(bulk=0.3, interactive=0.3)
    ) as worker:
        await run_until_finished(worker, admin, [slow_ok, too_slow], timeout=30)
    assert admin.execute("select status, error_code from app.jobs where id = %s", (slow_ok,)).fetchone() == (
        "done",
        None,
    )
    assert admin.execute("select status, error_code from app.jobs where id = %s", (too_slow,)).fetchone() == (
        "failed",
        "timed_out",
    )


# --- BG-05 / BG-06: a queue row starts a job only from a state a worker may start it from ----------------------------


async def test_a_queue_row_for_a_running_job_does_not_run_it_a_second_time_but_a_stalled_one_is_resumed(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case = owned_case(seed)
    registry = isolated_registry()
    ran: list[uuid.UUID] = []

    @registry.kind("zz_work", changes_case=False)
    async def work(ctx: JobContext) -> None:
        ran.append(ctx.job_id)

    queue = registry.queue_for("zz_work")
    running = seed.job(case, kind="zz_work", requested_by=expert)
    admin.execute("update app.jobs set status = 'running' where id = %s", (running,))
    defer(admin, queue, running, case)  # a duplicate row for a job another worker is running
    stalled = seed.job(case, kind="zz_work", requested_by=expert)
    admin.execute("update app.jobs set status = 'running', requeued_stall = true where id = %s", (stalled,))
    defer(admin, queue, stalled, case)  # the stall requeue's row: the worker's own marker is set
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_finished(worker, admin, [stalled], timeout=20)
        await asyncio.sleep(0.4)
    assert ran == [stalled]
    assert admin.execute("select status from app.jobs where id = %s", (running,)).fetchone() == ("running",)


@pytest.mark.parametrize("code", ["service_busy", "model_unavailable", "resuming", "busy_processing"])
async def test_a_queue_row_resumes_a_job_paused_for_a_reason_a_worker_may_resume(
    code: str, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case = owned_case(seed)
    registry = isolated_registry()
    ran: list[uuid.UUID] = []

    @registry.kind("zz_work", changes_case=False)
    async def work(ctx: JobContext) -> None:
        ran.append(ctx.job_id)

    job = seed.job(case, kind="zz_work", requested_by=expert)
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'paused', error_code = %s where id = %s", (code, job))
    defer(admin, registry.queue_for("zz_work"), job, case)
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        task = asyncio.create_task(worker.run())
        try:
            await wait_for(lambda: job_row(admin, job)[0] == "done", timeout=20)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    assert ran == [job]


# --- BG-09: the database decides a kind's queue and priority ---------------------------------------------------------


def test_the_enqueue_function_refuses_a_priority_other_than_the_two_the_app_uses(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case = owned_case(seed)
    with committing_api(api_dsn, expert) as api:
        for bad in (-1, 5, 11, 1000000):
            with pytest.raises(psycopg.Error) as refused:
                enqueue(api, case, "zz_prio", priority=bad)
            assert refused.value.sqlstate == "22023", bad
        assert enqueue(api, case, "zz_prio", priority=0) is not None
        assert enqueue(api, case, "zz_prio", priority=10) is not None


def test_a_listed_kind_takes_its_priority_and_queue_from_the_database(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    """`export_case` is interactive: asking for the bulk queue, or for it at priority 0, is refused."""
    expert, case = owned_case(seed)
    with committing_api(api_dsn, expert) as api:
        for queue, priority in (("bulk", 10), ("interactive", 0), ("bulk", 0)):
            with pytest.raises(psycopg.Error) as refused:
                enqueue(api, case, "export_case", queue=queue, priority=priority)
            assert refused.value.sqlstate == "22023", (queue, priority)
        job = enqueue(api, case, "export_case", queue="interactive", priority=10)
    assert job is not None


def test_a_listed_bulk_kind_cannot_be_put_on_the_interactive_queue(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case = owned_case(seed)
    with committing_api(api_dsn, expert) as api:
        with pytest.raises(psycopg.Error) as refused:
            enqueue(api, case, "process_bundle", queue="interactive", priority=10)
        assert refused.value.sqlstate == "22023"
        assert enqueue(api, case, "process_bundle", queue="bulk", priority=0) is not None


def test_every_registered_job_kind_has_a_row_whose_interactive_flag_is_the_registrys() -> None:
    """The registry says which queue and priority a kind has; the database repeats it to check the caller. A slice
    that adds a kind adds `(kind, action, interactive)` to `app.job_kind_actions` (ask foundation-owner)."""
    import app.worker.main  # noqa: F401 - registers the foundation's kinds
    from app.core.jobs import registry

    load_features(DEFAULT_PACKAGE)
    import os

    with psycopg.connect(os.environ["TEST_DATABASE_ADMIN_URL"]) as conn:
        rows = {r[0]: r[1] for r in conn.execute("select kind, interactive from app.job_kind_actions").fetchall()}
    missing = sorted(k for k in registry.kinds if rows.get(k) is None)
    assert not missing, f"no interactive flag in app.job_kind_actions for: {missing}"
    wrong = sorted(k for k, found in registry.kinds.items() if rows[k] != found.interactive)
    assert not wrong, f"the database and the registry disagree about the queue of: {wrong}"


# --- BG-11: a cap on queued or running jobs of one kind per case ---------------------------------------------------


async def test_a_case_cannot_hold_more_than_five_queued_or_running_jobs_of_one_kind(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case = owned_case(seed)
    _, other_case = owned_case(seed)
    other_expert = admin.execute("select named_expert_id from app.cases where id = %s", (other_case,)).fetchone()
    assert other_expert is not None
    jobs = []
    with committing_api(api_dsn, expert) as api:
        for _ in range(5):
            row = enqueue(api, case, "zz_cap")
            jobs.append(row[0])
        with pytest.raises(psycopg.Error) as refused:
            enqueue(api, case, "zz_cap")
        assert refused.value.sqlstate == QUEUE_FULL
        assert (await _database_error(cast(Request, SimpleNamespace()), refused.value)).status_code == 429
        assert enqueue(api, case, "zz_cap_other") is not None, "the cap is per kind"
        # finishing one makes room
        admin.execute("update app.jobs set status = 'running' where id = %s", (jobs[0],))
        admin.execute("update app.jobs set status = 'done' where id = %s", (jobs[0],))
        assert enqueue(api, case, "zz_cap") is not None
    with committing_api(api_dsn, other_expert[0]) as api:
        assert enqueue(api, other_case, "zz_cap") is not None, "the cap is per case"


def test_the_cap_error_is_a_429_with_a_plain_code() -> None:
    sent = too_many_jobs()
    assert (sent.status, sent.code) == (429, "too_many_jobs")


def test_a_worker_inside_its_own_job_is_not_held_to_the_cap(worker_dsn: str, seed: Seed) -> None:
    _, case = owned_case(seed)
    with role_connection(worker_dsn) as worker:
        worker.execute("select set_config('app.job_case_id', %s, true)", (str(case),))
        for _ in range(8):
            assert enqueue(worker, case, "zz_cap") is not None


def test_a_kind_that_is_queued_in_bulk_by_a_person_has_a_higher_cap() -> None:
    import os

    with psycopg.connect(os.environ["TEST_DATABASE_ADMIN_URL"]) as conn:
        rows: dict[str, int | None] = dict(conn.execute("select kind, max_active from app.job_kind_actions").fetchall())
    assert rows["finalize_upload"] is not None and rows["finalize_upload"] >= 100
    assert rows["process_bundle"] is None, "an ordinary kind takes the default of five"


# --- BG-14: pruning -------------------------------------------------------------------------------------------------


def _finished_queue_row(
    admin: psycopg.Connection, queue: str, job: uuid.UUID, case: uuid.UUID, *, age_days: int, status: str = "succeeded"
) -> int:
    defer(admin, queue, job, case)
    admin.execute("set search_path = procrastinate, public")  # the queue's status trigger names its tables bare
    try:
        found = admin.execute(
            "update procrastinate.procrastinate_jobs set status = %s::procrastinate.procrastinate_job_status,"
            " scheduled_at = now() - make_interval(days => %s) where args ->> 'job_id' = %s returning id",
            (status, age_days, str(job)),
        ).fetchone()
    finally:
        admin.execute("reset search_path")
    assert found is not None
    admin.execute(
        "update procrastinate.procrastinate_events set at = now() - make_interval(days => %s) where job_id = %s",
        (age_days, found[0]),
    )
    return int(found[0])


def _exists(admin: psycopg.Connection, queue_id: int) -> bool:
    return (
        admin.execute("select 1 from procrastinate.procrastinate_jobs where id = %s", (queue_id,)).fetchone()
        is not None
    )


def test_the_prune_removes_old_finished_queue_rows_and_keeps_recent_and_unfinished_ones(
    worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case = owned_case(seed)
    queue = f"zz_prune_{uuid.uuid4().hex[:8]}"
    old_done = _finished_queue_row(admin, queue, seed.job(case, kind="zz", requested_by=expert), case, age_days=60)
    old_failed = _finished_queue_row(
        admin, queue, seed.job(case, kind="zz", requested_by=expert), case, age_days=60, status="failed"
    )
    recent = _finished_queue_row(admin, queue, seed.job(case, kind="zz", requested_by=expert), case, age_days=3)
    waiting = _finished_queue_row(
        admin, queue, seed.job(case, kind="zz", requested_by=expert), case, age_days=60, status="todo"
    )
    with psycopg.connect(worker_dsn, autocommit=True) as worker:
        removed = worker.execute("select app.prune_queue_rows(30, 1000)").fetchone()
    assert removed is not None and removed[0] >= 2
    assert not _exists(admin, old_done) and not _exists(admin, old_failed)
    assert _exists(admin, recent) and _exists(admin, waiting)


def test_a_deleted_cases_finished_queue_rows_go_at_once_but_its_unfinished_ones_stay(
    worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case = owned_case(seed)
    queue = f"zz_prune_{uuid.uuid4().hex[:8]}"
    done = _finished_queue_row(admin, queue, seed.job(case, kind="zz", requested_by=expert), case, age_days=0)
    admin.execute("update app.cases set status = 'deleted' where id = %s", (case,))
    live_expert, live_case = owned_case(seed)
    live = _finished_queue_row(
        admin, queue, seed.job(live_case, kind="zz", requested_by=live_expert), live_case, age_days=0
    )
    with psycopg.connect(worker_dsn, autocommit=True) as worker:
        worker.execute("select app.prune_queue_rows(30, 1000)")
    assert not _exists(admin, done)
    assert _exists(admin, live)


def test_the_api_role_cannot_call_the_prune(api_dsn: str) -> None:
    with psycopg.connect(api_dsn, autocommit=True) as api:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute("select app.prune_queue_rows(0, 10)")


def test_purging_a_case_removes_its_finished_queue_rows(worker_dsn: str, admin: psycopg.Connection, seed: Seed) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    queue = f"zz_purge_{uuid.uuid4().hex[:8]}"
    finished = _finished_queue_row(admin, queue, seed.job(case, kind="zz", requested_by=expert), case, age_days=0)
    admin.execute("update app.cases set status = 'served' where id = %s", (case,))
    admin.execute(
        "insert into app.case_keys (case_id, wrapped_key, key_ref) values (%s, %s, 'zz-key') on conflict do nothing",
        (case, b"wrapped"),
    )
    with psycopg.connect(worker_dsn, autocommit=True) as worker:
        worker.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
        worker.execute("select app.purge_case(%s)", (case,))
    assert not _exists(admin, finished)
