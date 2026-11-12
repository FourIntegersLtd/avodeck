"""Background jobs, audit 15, area D (resource abuse) and the queue tables' growth (probe 20).

14 retry loops, 15 a flood of enqueues, 16 one person's backlog against everyone else's work, 17 an overall job time
limit, 20 how big the queue tables get and whether they are ever pruned (audit 12 D1f covers the audit and event
tables; these are the queue library's own).

Every test asserts the safe behaviour; a failing one is a confirmed weakness (strict xfail with its BG id) and
carries a control that shows the same path works for the legitimate action.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import psycopg
import pytest
from fastapi import FastAPI

from app.core.config import DEFAULT_API_RATE_PER_MINUTE
from app.core.db import Database
from app.core.jobs import JobContext, JobWorker
from tests.support.auth import APP_ORIGIN
from tests.support.background_jobs import defer
from tests.support.db import ClientFor
from tests.support.files import build_app
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def auth_env() -> dict[str, str]:
    dummy = {"SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_ANON_KEY": "zz", "SUPABASE_SERVICE_ROLE_KEY": "zz"}
    return {**dummy, "APP_ORIGIN": APP_ORIGIN}


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    built = build_app(api_db, auth_env)
    built.state.api_rate_per_minute = DEFAULT_API_RATE_PER_MINUTE  # what `create_app` sets from the cell's limits
    return built


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=8)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@contextmanager
def committing_api(dsn: str, person: uuid.UUID) -> Iterator[psycopg.Connection]:
    """A restricted API connection whose statements commit (so a second connection sees them), person set."""
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=20)
    try:
        conn.execute("select set_config('app.person_id', %s, false)", (str(person),))
        yield conn
    finally:
        conn.close()


# --- 14. retry loops ------------------------------------------------------------------------------------------------


async def test_a_job_that_always_fails_is_tried_max_attempts_times_and_then_stays_failed(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, api_db: Database, seed: Seed
) -> None:
    """Control for 14: the automatic retry loop is bounded."""
    from tests.support.db import scope_for

    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()
    runs: list[uuid.UUID] = []

    @registry.kind("zz_always_fails", changes_case=False)
    async def always_fails(ctx: JobContext) -> None:
        runs.append(ctx.job_id)
        raise RuntimeError("zz")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_always_fails")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        done = await run_until_finished(worker, admin, [job])
    assert done[job] == "failed"
    assert len(runs) == worker.max_attempts == 3
    assert admin.execute("select attempts from app.jobs where id = %s", (job,)).fetchone() == (3,)


def _fail_and_put_back(api: psycopg.Connection, admin: psycopg.Connection, job: uuid.UUID) -> bool:
    """One lap: the job fails for good (3 attempts used), then the person's retry function is called."""
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'failed', error_code = 'job_error', attempts = 3 where id = %s", (job,))
    row = api.execute("select app.requeue_failed_job(%s, 'zz_q', 0, false)", (job,)).fetchone()
    if row is None:
        raise RuntimeError("requeue_failed_job returned no row")
    return bool(row[0])


def test_control_the_first_retry_of_a_failed_job_is_allowed(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = seed.job(case, requested_by=expert)
    with committing_api(api_dsn, expert) as api:
        assert _fail_and_put_back(api, admin, job) is True


def test_a_failed_job_cannot_be_put_back_without_end(api_dsn: str, admin: psycopg.Connection, seed: Seed) -> None:
    """Eight laps at most; the first refusal ends the loop (the job is then still `failed`, and a failed job cannot
    be moved to `running` again, so a further lap is not a retry but a database error)."""
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = seed.job(case, requested_by=expert)
    outcomes: list[bool] = []
    with committing_api(api_dsn, expert) as api:
        for _ in range(8):
            accepted = _fail_and_put_back(api, admin, job)
            outcomes.append(accepted)
            if not accepted:
                break
    assert not all(outcomes) and len(outcomes) < 8, f"all {len(outcomes)} retries were accepted, none refused"


# --- 15. a flood of enqueues ----------------------------------------------------------------------------------------


async def test_control_one_process_request_queues_one_job(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    client = await client_for(expert)
    assert (await client.post(f"/v1/cases/{case}/process")).status_code == 202
    queued = admin.execute("select count(*) from app.jobs where case_id = %s and status = 'queued'", (case,)).fetchone()
    assert queued == (1,)


async def test_two_hundred_process_requests_do_not_queue_two_hundred_jobs(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    client = await client_for(expert)
    codes = [(await client.post(f"/v1/cases/{case}/process")).status_code for _ in range(200)]
    row = admin.execute("select count(*) from app.jobs where case_id = %s and status = 'queued'", (case,)).fetchone()
    if row is None:
        raise RuntimeError("count returned no row")
    queued = row[0]
    assert queued <= 5 or codes.count(429) > 100, f"{queued} jobs queued for one case ({codes.count(429)} refused)"


def test_control_the_enqueue_function_accepts_an_ordinary_priority(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    with committing_api(api_dsn, expert) as api:
        assert api.execute("select app.enqueue_job(%s, 'zz_a', 'zz_q', 0, false, 'x')", (case,)).fetchone() is not None


def test_the_enqueue_function_does_not_trust_the_callers_priority(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    with committing_api(api_dsn, expert) as api:
        try:
            row = api.execute("select app.enqueue_job(%s, 'zz_b', 'zz_q', 1000000, false, 'x')", (case,)).fetchone()
        except psycopg.Error:
            return  # refused: safe
    if row is None:
        raise RuntimeError("enqueue_job returned no row")
    stored = admin.execute("select priority from app.jobs where id = %s", (row[0],)).fetchone()
    if stored is None:
        raise RuntimeError("the job row is missing")
    assert stored[0] <= 10, f"stored priority {stored[0]}"


# --- 16. one person's backlog and everyone else's work ---------------------------------------------------------------


async def _position_of_the_other_cases_job(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed, *, priority: int
) -> int:
    """Twelve jobs of one case are queued, then one job of another case: how many started before the latter."""
    a, b = seed.person(), seed.person()
    case_a = seed.case(seed.org(kind="personal", members={a: "owner"}), a)
    case_b = seed.case(seed.org(kind="personal", members={b: "owner"}), b)
    registry = isolated_registry()
    order: list[uuid.UUID] = []

    @registry.kind("zz_work", changes_case=False)
    async def work(ctx: JobContext) -> None:
        order.append(ctx.job_id)
        await asyncio.sleep(0.2)

    queue = registry.queue_for("zz_work")
    backlog = [seed.job(case_a, kind="zz_work", requested_by=a) for _ in range(12)]
    for job in backlog:
        defer(admin, queue, job, case_a)
    mine = seed.job(case_b, kind="zz_work", requested_by=b)
    defer(admin, queue, mine, case_b, priority=priority)
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        try:
            done = await run_until_finished(worker, admin, [*backlog, mine], concurrency=2)
        except AssertionError as exc:  # a timeout is a broken setup, not the finding
            raise RuntimeError(f"setup: the worker did not finish the jobs: {exc}") from exc
    if set(done.values()) != {"done"}:
        raise RuntimeError(f"setup: not every job ended done: {done}")
    return order.index(mine)


async def test_control_a_higher_priority_job_overtakes_another_cases_backlog(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    assert await _position_of_the_other_cases_job(worker_db, worker_dsn, admin, seed, priority=10) <= 2


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BG-11: a queue is first in, first out within a priority and nothing shares it fairly between cases or"
    " people, so one case's backlog of ordinary jobs delays everyone else's ordinary jobs",
)
async def test_one_cases_backlog_does_not_hold_up_another_cases_ordinary_job(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    position = await _position_of_the_other_cases_job(worker_db, worker_dsn, admin, seed, priority=0)
    assert position <= 5, f"another case's job started {position + 1}th, after {position} of one case's earlier jobs"


# --- 17. an overall time limit ---------------------------------------------------------------------------------------


def test_a_job_has_an_overall_time_limit() -> None:
    names = set(inspect.signature(JobWorker.__init__).parameters)
    limits = {n for n in names if any(word in n for word in ("runtime", "time_limit", "max_seconds", "job_timeout"))}
    source = inspect.getsource(JobWorker)
    assert "stalled_after_seconds" in names, "control: the stall timeout (a dead worker) exists"
    assert limits or "asyncio.timeout(" in source or "wait_for(" in source, "no overall run-time limit on a job"


# --- 20. growth of the queue tables ---------------------------------------------------------------------------------


def _queue_index_definitions(admin: psycopg.Connection) -> list[str]:
    return [
        r[0]
        for r in admin.execute(
            "select indexdef from pg_indexes where schemaname = 'procrastinate' and tablename = 'procrastinate_jobs'"
        ).fetchall()
    ]


def test_control_the_queue_table_has_its_own_indexes(admin: psycopg.Connection) -> None:
    assert _queue_index_definitions(admin), "the queue table has indexes to look in"


def test_the_queue_has_an_index_the_orphan_sweep_and_the_run_lookup_can_use(admin: psycopg.Connection) -> None:
    """`app.requeue_orphaned_jobs` and the cancel/retry paths look queue rows up by `args ->> 'job_id'`."""
    indexes = _queue_index_definitions(admin)
    assert any("args" in d and "job_id" in d for d in indexes), "no index on the job id inside the queue payload"


async def test_finished_queue_rows_are_pruned_by_a_worker_housekeeping_round(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, api_db: Database, seed: Seed
) -> None:
    """A finished queue row (and its events) that is a year old is gone after one housekeeping round."""
    from tests.support.db import scope_for

    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()

    @registry.kind("zz_quick", changes_case=False)
    async def quick(ctx: JobContext) -> None:
        return None

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_quick")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        try:
            await run_until_finished(worker, admin, [job])
        except AssertionError as exc:  # a timeout is a broken setup, not the finding
            raise RuntimeError(f"setup: the job did not finish: {exc}") from exc
        queue_ids = [
            r[0]
            for r in admin.execute(
                "select id from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(job),)
            ).fetchall()
        ]  # noqa: E501
        if len(queue_ids) != 1:
            raise RuntimeError(f"setup: expected one queue row, found {len(queue_ids)}")
        admin.execute(
            "update procrastinate.procrastinate_jobs set scheduled_at = now() - interval '400 days' where id = %s",
            (queue_ids[0],),
        )
        admin.execute(
            "update procrastinate.procrastinate_events set at = now() - interval '400 days' where job_id = %s",
            (queue_ids[0],),
        )
        housekeeping = asyncio.create_task(worker._housekeeping())  # noqa: SLF001
        try:
            await asyncio.sleep(1.5)  # a round runs at once; the next is stalled_after_seconds / 2 away
        finally:
            housekeeping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await housekeeping
    left = admin.execute(
        "select (select count(*) from procrastinate.procrastinate_jobs where id = %s),"
        " (select count(*) from procrastinate.procrastinate_events where job_id = %s)",
        (queue_ids[0], queue_ids[0]),
    ).fetchone()
    assert left == (0, 0), f"a year-old finished queue row survived the housekeeping round (rows, events): {left}"


def test_the_deferral_function_is_the_only_writer_the_api_role_reaches(admin: psycopg.Connection) -> None:
    """Control for the growth finding: the API role cannot fill the queue tables except through `app.enqueue_job`."""
    row = admin.execute(
        "select has_table_privilege('app_api', 'procrastinate.procrastinate_jobs', 'insert'),"
        " has_function_privilege('app_api', 'app.enqueue_job(uuid, text, text, integer, boolean, text)', 'execute')"
    ).fetchone()
    assert row == (False, True)
