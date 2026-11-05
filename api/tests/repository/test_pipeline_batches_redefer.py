"""`app.pipeline_redefer` and `app.pipeline_case_busy` (migration 0137, ADR 0038, F57, F58, F67, F68).

`pipeline_redefer(job, wake)` puts a new queue row on an EXISTING job (never a second job): a wake for a running
parent, a hand-back for a paused waiter. `pipeline_case_busy(case, resumable)` says whether a waiter must keep
waiting. Both are definer functions the worker role may call and the API role may not.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core import jobs
from app.core.db import Database
from app.core.jobs import JobContext, JobParked
from tests.repository.test_pipeline_batches_support import (
    WAKE_PRIORITY,
    add_batch,
    add_row,
    add_run,
    child_job,
    job_state,
    make_job,
    rows_of,
)
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker
from tests.support.seed import Seed

RESUMABLE = ["service_busy", "model_unavailable"]


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(members={expert: "owner"}), expert)


def redefer(admin: psycopg.Connection, job: uuid.UUID, wake: bool) -> bool:
    row = admin.execute("select app.pipeline_redefer(%s, %s)", (job, wake)).fetchone()
    assert row is not None
    return bool(row[0])


def busy(admin: psycopg.Connection, case: uuid.UUID) -> bool:
    row = admin.execute("select app.pipeline_case_busy(%s, %s)", (case, RESUMABLE)).fetchone()
    assert row is not None
    return bool(row[0])


def test_only_the_worker_may_call_the_functions(admin: psycopg.Connection, worker_dsn: str, api_dsn: str) -> None:
    for name in (
        "pipeline_redefer(uuid, boolean)",
        "pipeline_case_busy(uuid, text[])",
        "pipeline_defer_row(uuid, text, text, integer)",
    ):
        row = admin.execute(
            "select has_function_privilege('app_worker', %s::regprocedure, 'execute'),"
            " has_function_privilege('app_api', %s::regprocedure, 'execute'),"
            " has_function_privilege('public', %s::regprocedure, 'execute')",
            (f"app.{name}", f"app.{name}", f"app.{name}"),
        ).fetchone()
        assert row == (name.startswith(("pipeline_redefer", "pipeline_case_busy")), False, False), name


def test_a_wake_defers_one_row_for_the_existing_parent_with_the_latest_rows_queue_and_lock(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    add_row(admin, parent, case, queue="bulk", status="succeeded")
    jobs_before = admin.execute("select count(*) from app.jobs where case_id = %s", (case,)).fetchone()
    assert redefer(admin, parent, wake=True)
    rows = rows_of(admin, parent)
    assert rows[-1] == ("bulk", "todo", WAKE_PRIORITY, f"case:{case}")
    assert len(rows) == 2
    assert admin.execute("select count(*) from app.jobs where case_id = %s", (case,)).fetchone() == jobs_before
    status, code, attempts, stalled = job_state(admin, parent)
    assert (status, code, stalled) == ("running", None, True)
    assert attempts == 0


def test_a_waiter_keeps_its_own_priority_and_error_code(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    waiter = make_job(admin, seed, case, kind="reprocess_case", status="paused", code="busy_processing", priority=10)
    add_row(admin, waiter, case, queue="interactive", status="succeeded", priority=10)
    assert redefer(admin, waiter, wake=False)
    assert rows_of(admin, waiter)[-1] == ("interactive", "todo", 10, f"case:{case}")
    status, code, _, stalled = job_state(admin, waiter)
    assert (status, code, stalled) == ("paused", "busy_processing", False)


def test_a_job_without_any_earlier_row_gets_bulk_and_the_case_lock(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    assert redefer(admin, parent, wake=True)
    assert rows_of(admin, parent) == [("bulk", "todo", WAKE_PRIORITY, f"case:{case}")]


def test_nothing_changes_for_a_held_case_a_deleted_case_or_the_wrong_kind_of_job(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    paused_parent = make_job(admin, seed, case, status="paused", code="cancelled")
    done = make_job(admin, seed, case, status="done")
    admin.execute("update app.cases set on_hold = true where id = %s", (case,))
    assert not redefer(admin, parent, wake=True)
    admin.execute("update app.cases set on_hold = false where id = %s", (case,))
    assert not redefer(admin, paused_parent, wake=True)  # a wake needs a running job
    assert not redefer(admin, parent, wake=False)  # a hand-back needs a paused job
    assert not redefer(admin, done, wake=True)
    assert not redefer(admin, uuid.uuid4(), wake=True)
    assert all(not rows_of(admin, j) for j in (parent, paused_parent, done))
    admin.execute("update app.cases set status = 'deleted' where id = %s", (case,))
    assert not redefer(admin, parent, wake=True)
    assert not rows_of(admin, parent)


@pytest.mark.parametrize("existing", ["todo", "doing"])
def test_a_job_that_already_has_a_waiting_or_running_row_gets_no_second_one(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, existing: str
) -> None:
    parent = make_job(admin, seed, case)
    add_row(admin, parent, case, status=existing)
    assert not redefer(admin, parent, wake=True)
    assert redefer(admin, parent, wake=True) is False
    assert len(rows_of(admin, parent)) == 1


def test_calling_twice_makes_one_row(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case)
    assert redefer(admin, parent, wake=True)
    assert not redefer(admin, parent, wake=True)
    assert len(rows_of(admin, parent)) == 1


def test_a_case_with_no_open_run_is_not_busy(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    assert not busy(admin, case)
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "done")
    assert not busy(admin, case)


@pytest.mark.parametrize("parent_status", ["running", "queued"])
def test_an_open_run_with_a_live_parent_is_busy(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, parent_status: str
) -> None:
    parent = make_job(admin, seed, case, status=parent_status)
    add_run(admin, case, parent, "ocr_waiting")
    assert busy(admin, case)


def test_a_child_with_a_queue_row_makes_the_case_busy(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case, status="paused", code="cancelled")
    run = add_run(admin, case, parent, "ocr_waiting")
    child = child_job(admin, seed, case)
    add_batch(admin, run, case, 0, child=child)
    assert not busy(admin, case)
    add_row(admin, child, case, lock=None, status="todo")
    assert busy(admin, case)


def test_a_parent_paused_with_a_resumable_code_is_busy_but_a_stop_or_a_failure_is_not(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    for code, expected in (
        ("service_busy", True),
        ("model_unavailable", True),
        ("cancelled", False),
        ("cost_guard", False),
    ):
        parent = make_job(admin, seed, case, status="paused", code=code)
        add_run(admin, case, parent, "finishing")
        assert busy(admin, case) is expected, code
        admin.execute("update app.pipeline_runs set state = 'closed' where job_id = %s", (parent,))
    failed = make_job(admin, seed, case, status="failed", code="zz_failed")
    add_run(admin, case, failed, "sort_waiting")
    assert not busy(admin, case)


def test_children_paused_service_busy_with_sleeping_rows_and_the_parent_mirrored_are_busy(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case, status="paused", code="service_busy")
    run = add_run(admin, case, parent, "ocr_waiting")
    for seq in range(2):
        child = child_job(admin, seed, case, status="paused", code="service_busy")
        add_batch(admin, run, case, seq, child=child)
        add_row(admin, child, case, lock=None, delay_seconds=3600)
    assert busy(admin, case)


def test_a_child_row_that_has_ended_does_not_count(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case, status="paused", code="cancelled")
    run = add_run(admin, case, parent, "ocr_waiting")
    child = child_job(admin, seed, case)
    add_batch(admin, run, case, 0, child=child)
    add_row(admin, child, case, lock=None, status="succeeded")
    assert not busy(admin, case)


# --- the function definitions ---------------------------------------------------------------------------------------


def test_every_definer_function_pins_its_search_path_and_none_reaches_public(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "select p.proname, p.prosecdef, coalesce(p.proconfig, '{}') from pg_proc p"
        " join pg_namespace n on n.oid = p.pronamespace"
        " where n.nspname = 'app' and p.proname like 'pipeline\\_%' and p.prorettype <> 'trigger'::regtype"
    ).fetchall()
    assert len(rows) >= 13
    for name, definer, config in rows:
        paths = [c for c in config if c.startswith("search_path=")]
        assert definer, name
        assert len(paths) == 1, name
        assert "public" not in paths[0], name


def test_the_top_up_creates_its_child_through_enqueue_job_not_a_direct_insert(admin: psycopg.Connection) -> None:
    source = admin.execute("select prosrc from pg_proc where proname = 'pipeline_topup'").fetchone()
    assert source is not None
    assert "app.enqueue_job" in source[0]
    assert "insert into app.jobs" not in source[0]


# --- the wake does not count an attempt (F65) --------------------------------------------------------------------


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def latest_row_status(admin: psycopg.Connection, job: uuid.UUID) -> str | None:
    found = admin.execute(
        "select status::text from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s order by id desc",
        (str(job),),
    ).fetchone()
    return None if found is None else str(found[0])


async def run_until_row_ends(worker: jobs.JobWorker, admin: psycopg.Connection, job: uuid.UUID) -> None:
    task = asyncio.create_task(worker.run())
    deadline = time.monotonic() + 30
    try:
        while latest_row_status(admin, job) not in ("succeeded", "failed"):
            assert time.monotonic() < deadline, "the queue row did not end"
            assert not task.done()
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.anyio
async def test_start_wake_wake_keeps_one_attempt(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()
    attempts: list[int] = []

    @registry.kind("zz_wakes_twice", changes_case=True)
    async def wakes_twice(ctx: JobContext) -> None:
        attempts.append(ctx.attempt)
        if len(attempts) < 3:
            raise JobParked

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_wakes_twice")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_row_ends(worker, admin, job)
        assert job_state(admin, job)[:3] == ("running", None, 1)
        assert redefer(admin, job, wake=True)  # wake A
        await run_until_row_ends(worker, admin, job)
        assert job_state(admin, job)[:3] == ("running", None, 1)
        assert redefer(admin, job, wake=True)  # wake B
        await run_until_row_ends(worker, admin, job)
    assert attempts == [1, 1, 1]
    assert job_state(admin, job)[:3] == ("done", None, 1)


@pytest.mark.anyio
async def test_a_tail_error_after_two_wakes_is_retried_to_the_maximum(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "_dead_letter", lambda kind, job: None)
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()
    attempts: list[int] = []

    @registry.kind("zz_tail_error", changes_case=True)
    async def tail_error(ctx: JobContext) -> None:
        attempts.append(ctx.attempt)
        if len(attempts) < 3:
            raise JobParked
        raise RuntimeError("zz")  # a tail failure: the type only is logged

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_tail_error")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_row_ends(worker, admin, job)
        assert redefer(admin, job, wake=True)
        await run_until_row_ends(worker, admin, job)
        assert redefer(admin, job, wake=True)
        await run_until_row_ends(worker, admin, job)
    assert attempts == [1, 1, 1, 2, 3], "the wakes added none; each failure after them counted one"
    assert job_state(admin, job)[:3] == ("failed", "job_error", 3)


# --- the fetch (the queue's own lock rule) ----------------------------------------------------------------------


def test_a_sleeping_row_of_another_job_with_a_lower_id_and_priority_does_not_block_the_wake(
    admin: psycopg.Connection, admin_dsn: str, seed: Seed, case: uuid.UUID
) -> None:
    """Another job's `todo` row on the case lock (priority 10, backing off, lower id) must not stop the fetch from
    giving the worker the parent's wake (priority 20): checked with the queue's own fetch, not a reading of it."""
    queue = f"zz_fetch_{uuid.uuid4().hex[:10]}"
    sleeper = make_job(admin, seed, case, kind="reprocess_case", status="paused", code="busy_processing", priority=10)
    add_row(admin, sleeper, case, queue=queue, priority=10, delay_seconds=3600)
    parent = make_job(admin, seed, case)
    add_row(admin, parent, case, queue=queue, status="succeeded")
    assert redefer(admin, parent, wake=True)
    wake_row = max(
        int(r[0])
        for r in admin.execute(
            "select id from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(parent),)
        ).fetchall()
    )
    with psycopg.connect(admin_dsn, autocommit=True, options="-c search_path=procrastinate") as q:
        worker_id = q.execute("select procrastinate_register_worker_v1()").fetchone()[0]  # type: ignore[index]
        try:
            taken = q.execute("select id from procrastinate_fetch_job_v2(%s, %s)", ([queue], worker_id)).fetchone()
        finally:
            q.execute("update procrastinate_jobs set status = 'cancelled' where queue_name = %s", (queue,))
    assert taken is not None and int(taken[0]) == wake_row
