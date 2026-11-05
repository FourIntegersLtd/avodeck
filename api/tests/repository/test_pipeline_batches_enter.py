"""`app.pipeline_enter` and `app.pipeline_reopen_run` (migration 0137, ADR 0038, F57, F60, F66).

`pipeline_enter(run)` is the one call at the top of a batch-mode parent: it resets its own queue row's priority, takes
the run row lock, and says what to do (`end`, `wake_a`, `tail` or `park`) without ever queuing a second row.
`pipeline_reopen_run(run)` puts failed and paused children back on the queue, with no case lock.
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
    run_state,
)
from tests.support.seed import Seed


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(members={expert: "owner"}), expert)


def as_job_on(conn: psycopg.Connection, case: uuid.UUID) -> None:
    conn.execute("select set_config('app.job_case_id', %s, false)", (str(case),))


def enter(conn: psycopg.Connection, case: uuid.UUID, run: uuid.UUID) -> str:
    as_job_on(conn, case)
    row = conn.execute("select app.pipeline_enter(%s)", (run,)).fetchone()
    assert row is not None
    return str(row[0])


def reopen(conn: psycopg.Connection, case: uuid.UUID, run: uuid.UUID) -> None:
    as_job_on(conn, case)
    conn.execute("select app.pipeline_reopen_run(%s)", (run,))


def parent_with_row(admin: psycopg.Connection, seed: Seed, case: uuid.UUID, state: str, **kw: int) -> uuid.UUID:
    """A running parent whose own queue row is `doing` (the handler is running), and its run."""
    parent = make_job(admin, seed, case)
    add_row(admin, parent, case, status="doing", **kw)
    add_run(admin, case, parent, state)
    return parent


@pytest.mark.parametrize(
    ("state", "answer", "after"),
    [
        ("done", "end", "done"),
        ("closed", "end", "closed"),
        ("ocr_ready", "wake_a", "ocr_ready"),
        ("sort_ready", "tail", "finishing"),
        ("finishing", "tail", "finishing"),
    ],
)
def test_enter_answers_for_every_state_without_a_second_queue_row(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, state: str, answer: str, after: str
) -> None:
    parent = parent_with_row(admin, seed, case, state)
    assert enter(admin, case, parent) == answer
    assert run_state(admin, parent) == after
    assert len(rows_of(admin, parent)) == 1


def test_enter_for_a_run_that_is_gone_ends(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    assert enter(admin, case, uuid.uuid4()) == "end"


@pytest.mark.parametrize(
    ("state", "answer", "after"), [("ocr_waiting", "wake_a", "ocr_ready"), ("sort_waiting", "tail", "finishing")]
)
def test_enter_on_a_waiting_run_with_nothing_open_moves_it_on_without_a_queue_row(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, state: str, answer: str, after: str
) -> None:
    parent = parent_with_row(admin, seed, case, state)
    phase = state.split("_")[0]
    child = child_job(admin, seed, case, status="done")
    add_batch(admin, parent, case, 0, phase=phase, child=child, done=True)
    assert enter(admin, case, parent) == answer
    assert run_state(admin, parent) == after
    assert len(rows_of(admin, parent)) == 1


def test_enter_on_a_waiting_run_with_an_open_batch_parks_and_reopens(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = parent_with_row(admin, seed, case, "ocr_waiting")
    child = child_job(admin, seed, case, status="running")
    add_batch(admin, parent, case, 0, child=child)
    assert enter(admin, case, parent) == "park"
    assert run_state(admin, parent) == "ocr_waiting"
    row = admin.execute("select reopened_at is not null from app.pipeline_runs where job_id = %s", (parent,)).fetchone()
    assert row == (True,)


def test_enter_puts_its_own_rows_priority_back_to_the_jobs_own(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = parent_with_row(admin, seed, case, "ocr_ready", priority=WAKE_PRIORITY)
    other = make_job(admin, seed, case, kind="rerun_checks")
    add_row(admin, other, case, priority=WAKE_PRIORITY)  # another job's row is not touched
    assert enter(admin, case, parent) == "wake_a"
    assert rows_of(admin, parent) == [("bulk", "doing", 0, f"case:{case}")]
    assert rows_of(admin, other)[0][2] == WAKE_PRIORITY


def test_enter_for_another_case_is_refused(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = parent_with_row(admin, seed, case, "ocr_ready")
    as_job_on(admin, uuid.uuid4())
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute("select app.pipeline_enter(%s)", (parent,))


def test_only_the_worker_may_call_enter_and_reopen(admin: psycopg.Connection) -> None:
    for name in ("pipeline_enter(uuid)", "pipeline_reopen_run(uuid)"):
        row = admin.execute(
            "select has_function_privilege('app_worker', %s::regprocedure, 'execute'),"
            " has_function_privilege('app_api', %s::regprocedure, 'execute'),"
            " has_function_privilege('public', %s::regprocedure, 'execute')",
            (f"app.{name}",) * 3,
        ).fetchone()
        assert row == (True, False, False), name


# --- reopen -----------------------------------------------------------------------------------------------------


def waiting_run(admin: psycopg.Connection, seed: Seed, case: uuid.UUID, *, window: int = 2) -> uuid.UUID:
    parent = make_job(admin, seed, case)
    return add_run(admin, case, parent, "ocr_waiting", window=window)


def test_reopen_puts_a_failed_child_back_with_no_lock_and_no_attempts(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    run = waiting_run(admin, seed, case)
    child = child_job(admin, seed, case, status="failed", code="job_error")
    add_batch(admin, run, case, 0, child=child)
    add_row(admin, child, case, lock=None, status="succeeded")
    admin.execute("update app.jobs set attempts = 3 where id = %s", (child,))
    reopen(admin, case, run)
    assert job_state(admin, child)[:3] == ("queued", None, 0)
    assert rows_of(admin, child)[-1] == ("bulk", "todo", 0, None)
    reopen(admin, case, run)  # idempotent: no second row
    assert len(rows_of(admin, child)) == 2


def test_reopen_brings_a_sleeping_paused_child_forward_or_defers_a_row_for_one_without(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    run = waiting_run(admin, seed, case)
    sleeping = child_job(admin, seed, case, status="paused", code="service_busy")
    rowless = child_job(admin, seed, case, status="paused", code="service_busy")
    add_batch(admin, run, case, 0, child=sleeping)
    add_batch(admin, run, case, 1, child=rowless)
    add_row(admin, sleeping, case, lock=None, delay_seconds=3600)
    reopen(admin, case, run)
    rows = admin.execute(
        "select count(*), bool_and(scheduled_at <= now()) from procrastinate.procrastinate_jobs"
        " where args ->> 'job_id' = %s and status = 'todo'",
        (str(sleeping),),
    ).fetchone()
    assert rows == (1, True)
    assert rows_of(admin, rowless) == [("bulk", "todo", 0, None)]
    assert job_state(admin, sleeping)[:2] == ("paused", "service_busy")  # the worker's start makes it run


def test_reopen_leaves_running_queued_and_finished_children_alone(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    run = waiting_run(admin, seed, case, window=3)
    running = child_job(admin, seed, case, status="running")
    queued = child_job(admin, seed, case, status="queued")
    finished = child_job(admin, seed, case, status="done")
    add_batch(admin, run, case, 0, child=running)
    add_batch(admin, run, case, 1, child=queued)
    add_batch(admin, run, case, 2, child=finished, done=True)
    reopen(admin, case, run)
    assert [job_state(admin, c)[0] for c in (running, queued, finished)] == ["running", "queued", "done"]
    assert not any(rows_of(admin, c) for c in (running, queued, finished))


def test_reopen_tops_the_window_up_and_never_adds_a_second_child_for_a_batch(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    run = waiting_run(admin, seed, case, window=2)
    batches = [add_batch(admin, run, case, seq) for seq in range(4)]
    reopen(admin, case, run)
    reopen(admin, case, run)
    kids = admin.execute(
        "select count(child_job_id) from app.pipeline_batches where run_job_id = %s", (run,)
    ).fetchone()
    assert kids == (2,)
    assert len(batches) == 4


def test_reopen_sets_reopened_at_and_refuses_another_case(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    run = waiting_run(admin, seed, case)
    reopen(admin, case, run)
    row = admin.execute("select reopened_at is not null from app.pipeline_runs where job_id = %s", (run,)).fetchone()
    assert row == (True,)
    as_job_on(admin, uuid.uuid4())
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute("select app.pipeline_reopen_run(%s)", (run,))
