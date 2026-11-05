"""`app.pipeline_redefer` and `app.pipeline_case_busy` (migration 0137, ADR 0038, F57, F58, F67, F68).

`pipeline_redefer(job, wake)` puts a new queue row on an EXISTING job (never a second job): a wake for a running
parent, a hand-back for a paused waiter. `pipeline_case_busy(case, resumable)` says whether a waiter must keep
waiting. Both are definer functions the worker role may call and the API role may not.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

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
    for name in ("pipeline_redefer(uuid, boolean)", "pipeline_case_busy(uuid, text[])", "pipeline_defer_row(uuid, text, text, integer)"):
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
    for code, expected in (("service_busy", True), ("model_unavailable", True), ("cancelled", False), ("cost_guard", False)):
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
