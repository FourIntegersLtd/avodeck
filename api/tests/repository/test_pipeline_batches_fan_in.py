"""Fan-in: the last child of a phase wakes the parked parent exactly once (migration 0137, ADR 0038, F56, F59, F69).

`app.pipeline_complete_batch(batch)` runs in the child's own last transaction. It locks the run row, marks the batch
done, tops the window up, and, when no batch of the phase is open, the parent is `running` and no older run holds the
case, moves the run to `*_ready` and defers one queue row for the existing parent.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

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


def complete(conn: psycopg.Connection, case: uuid.UUID, batch: uuid.UUID) -> bool:
    as_job_on(conn, case)
    row = conn.execute("select app.pipeline_complete_batch(%s)", (batch,)).fetchone()
    assert row is not None
    return bool(row[0])


def two_batches(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, state: str = "ocr_waiting", **kw: Any
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    parent = make_job(admin, seed, case, **kw)
    run = add_run(admin, case, parent, state)
    phase = "ocr" if state.startswith("ocr") else "sort"
    batches: list[uuid.UUID] = []
    for seq in range(2):
        child = child_job(admin, seed, case, status="running")
        batches.append(add_batch(admin, run, case, seq, phase=phase, child=child))
    return parent, run, batches


def test_the_last_batch_of_a_phase_wakes_the_parent_once(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent, run, (first, second) = two_batches(admin, seed, case)
    add_row(admin, parent, case, status="succeeded")  # the parent's row that ended when it parked
    assert not complete(admin, case, first)
    assert run_state(admin, run) == "ocr_waiting"
    assert len(rows_of(admin, parent)) == 1
    assert complete(admin, case, second)
    assert run_state(admin, run) == "ocr_ready"
    assert rows_of(admin, parent)[-1] == ("bulk", "todo", WAKE_PRIORITY, f"case:{case}")
    assert len(rows_of(admin, parent)) == 2
    assert job_state(admin, parent)[3] is True  # requeued_stall: a wake is not an attempt
    assert admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'process_bundle'", (case,)
    ).fetchone() == (1,)
    assert not complete(admin, case, second)  # the same call again fires nothing
    assert len(rows_of(admin, parent)) == 2


def test_the_sort_phase_fires_into_sort_ready(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent, run, (first, second) = two_batches(admin, seed, case, "sort_waiting")
    assert not complete(admin, case, first)
    assert complete(admin, case, second)
    assert run_state(admin, run) == "sort_ready"


def test_no_fire_into_a_parent_that_is_not_running_f69(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent, run, (first, second) = two_batches(admin, seed, case, status="paused", code="service_busy")
    complete(admin, case, first)
    assert not complete(admin, case, second)
    assert run_state(admin, run) == "ocr_waiting"
    assert not rows_of(admin, parent)


def test_a_held_case_leaves_the_run_ready_for_the_sweep(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent, run, (first, second) = two_batches(admin, seed, case)
    admin.execute("update app.cases set on_hold = true where id = %s", (case,))
    complete(admin, case, first)
    complete(admin, case, second)
    assert run_state(admin, run) == "ocr_ready"
    assert not rows_of(admin, parent)


def test_a_younger_run_waits_for_an_older_run_with_a_running_parent_and_fires_when_it_ends(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    old_parent = make_job(admin, seed, case)
    old_run = add_run(admin, case, old_parent, "finishing")
    young_parent, young_run, (first, second) = two_batches(admin, seed, case)
    complete(admin, case, first)
    assert not complete(admin, case, second)
    assert run_state(admin, young_run) == "ocr_waiting"
    as_job_on(admin, case)
    assert admin.execute("select app.pipeline_finish_run(%s)", (old_run,)).fetchone() == (True,)
    assert run_state(admin, old_run) == "done"
    assert run_state(admin, young_run) == "ocr_ready"
    assert len(rows_of(admin, young_parent)) == 1


def test_an_older_run_whose_parent_is_paused_or_failed_does_not_hold_a_younger_one(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    for status, code in (("paused", "cancelled"), ("failed", "zz_failed")):
        other = make_job(admin, seed, case, status=status, code=code)
        add_run(admin, case, other, "ocr_waiting")
    parent, run, (first, second) = two_batches(admin, seed, case)
    complete(admin, case, first)
    assert complete(admin, case, second)
    assert run_state(admin, run) == "ocr_ready"


def test_finishing_a_run_that_is_not_finishing_does_nothing(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    run = add_run(admin, case, parent, "sort_ready")
    as_job_on(admin, case)
    assert admin.execute("select app.pipeline_finish_run(%s)", (run,)).fetchone() == (False,)
    assert run_state(admin, run) == "sort_ready"


def test_the_window_is_topped_up_one_batch_at_a_time_and_never_twice_for_one_batch(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    run = add_run(admin, case, parent, "ocr_waiting", window=2)
    first_child = child_job(admin, seed, case, status="running")
    second_child = child_job(admin, seed, case, status="queued")
    batches = [
        add_batch(admin, run, case, 0, child=first_child),
        add_batch(admin, run, case, 1, child=second_child),
        add_batch(admin, run, case, 2),
        add_batch(admin, run, case, 3),
    ]
    complete(admin, case, batches[0])
    rows = admin.execute(
        "select b.seq, j.kind from app.pipeline_batches b join app.jobs j on j.id = b.child_job_id"
        " where b.run_job_id = %s order by b.seq",
        (run,),
    ).fetchall()
    assert rows == [(0, "read_page_batch"), (1, "read_page_batch"), (2, "read_page_batch")]
    third_child = admin.execute("select child_job_id from app.pipeline_batches where id = %s", (batches[2],)).fetchone()
    assert third_child is not None
    assert rows_of(admin, third_child[0]) == [("bulk", "todo", 0, None)]
    complete(admin, case, batches[0])  # again: the window is full, nothing more is queued
    assert admin.execute(
        "select count(*) from app.pipeline_batches where run_job_id = %s and child_job_id is not null", (run,)
    ).fetchone() == (3,)


def test_a_rollback_between_completing_and_returning_leaves_nothing_and_the_retry_fires(
    admin_dsn: str, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent, run, (first, second) = two_batches(admin, seed, case)
    complete(admin, case, first)
    with psycopg.connect(admin_dsn, autocommit=False) as conn:
        as_job_on(conn, case)
        assert conn.execute("select app.pipeline_complete_batch(%s)", (second,)).fetchone() == (True,)
        conn.rollback()
    assert run_state(admin, run) == "ocr_waiting"
    assert not rows_of(admin, parent)
    assert complete(admin, case, second)
    assert len(rows_of(admin, parent)) == 1


def test_a_job_on_another_case_is_refused_and_a_missing_batch_returns_quietly(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent, run, (first, _) = two_batches(admin, seed, case)
    admin.execute("select set_config('app.job_case_id', %s, false)", (str(uuid.uuid4()),))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute("select app.pipeline_complete_batch(%s)", (first,))
    as_job_on(admin, case)
    assert admin.execute("select app.pipeline_complete_batch(%s)", (uuid.uuid4(),)).fetchone() == (False,)


def test_two_last_children_completing_at_once_wake_the_parent_exactly_once(
    admin_dsn: str, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent, run, batches = two_batches(admin, seed, case)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def finish(batch: uuid.UUID) -> None:
        try:
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                as_job_on(conn, case)
                barrier.wait(10)
                conn.execute("select app.pipeline_complete_batch(%s)", (batch,))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=finish, args=(b,)) for b in batches]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert run_state(admin, run) == "ocr_ready"
    assert len(rows_of(admin, parent)) == 1
