"""`app.pipeline_heal_runs` (migration 0137, ADR 0038, F56-F69): the sweep that heals a lost fire or top-up, re-wakes a
stranded parent, mirrors the children onto the parent, re-defers sleepers and hands waiters back.

It runs on the anonymous worker connection (no person, no `app.job_case_id`). Pass order inside one run, pinned below:
mirror, (i) child re-defer, (ii) parent re-defer, stranded-parent wake, top-up, fire.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

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

RESUMABLE = ["service_busy", "model_unavailable"]


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(members={expert: "owner"}), expert)


@pytest.fixture
def sweep(worker_dsn: str) -> Callable[[], int]:
    def run() -> int:
        with psycopg.connect(worker_dsn, autocommit=True) as conn:
            row = conn.execute("select app.pipeline_heal_runs(%s)", (RESUMABLE,)).fetchone()
            assert row is not None
            assert conn.execute("select app.current_job_case_id()").fetchone() == (None,)
            return int(row[0])

    return run


def end_rows(admin: psycopg.Connection, job: uuid.UUID) -> None:
    """The queue rows of the job end: todo -> doing -> succeeded."""
    admin.execute("set search_path = procrastinate, public")
    for status in ("doing", "succeeded"):
        admin.execute(
            "update procrastinate.procrastinate_jobs set status = %s where args ->> 'job_id' = %s", (status, str(job))
        )
    admin.execute("reset search_path")


def run_with(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, state: str = "ocr_waiting", *, window: int = 2, **kw: str
) -> uuid.UUID:
    parent = make_job(admin, seed, case, **kw)
    return add_run(admin, case, parent, state, window=window)


def kids(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, run: uuid.UUID, *statuses: str, code: str | None = None
):
    out = []
    for seq, status in enumerate(statuses):
        child = child_job(admin, seed, case, status=status, code=code)
        add_batch(admin, run, case, seq, child=child, done=status == "done")
        out.append(child)
    return out


def test_only_the_worker_may_call_the_sweep(admin: psycopg.Connection) -> None:
    row = admin.execute(
        "select has_function_privilege('app_worker', 'app.pipeline_heal_runs(text[])', 'execute'),"
        " has_function_privilege('app_api', 'app.pipeline_heal_runs(text[])', 'execute'),"
        " has_function_privilege('public', 'app.pipeline_heal_runs(text[])', 'execute')"
    ).fetchone()
    assert row == (True, False, False)


def test_a_lost_fire_and_a_lost_top_up_are_healed_by_one_sweep_and_a_second_is_a_no_op(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    fire = run_with(admin, seed, case)
    kids(admin, seed, case, fire, "done", "done")
    other = seed.case(seed.org(members={(p := seed.person()): "owner"}), p)
    topup = run_with(admin, seed, other, window=2)
    for seq in range(4):
        add_batch(admin, topup, other, seq)
    sweep()
    assert run_state(admin, fire) == "ocr_ready"
    assert len(rows_of(admin, fire)) == 1
    assert admin.execute(
        "select count(child_job_id) from app.pipeline_batches where run_job_id = %s", (topup,)
    ).fetchone() == (2,)
    before = (len(rows_of(admin, fire)), job_state(admin, fire), run_state(admin, fire))
    sweep()
    assert (len(rows_of(admin, fire)), job_state(admin, fire), run_state(admin, fire)) == before
    assert admin.execute(
        "select count(child_job_id) from app.pipeline_batches where run_job_id = %s", (topup,)
    ).fetchone() == (2,)
    assert admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'process_bundle'", (case,)
    ).fetchone() == (1,)


def test_a_held_case_is_left_alone_until_released(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case, "ocr_ready")
    admin.execute("update app.cases set on_hold = true where id = %s", (case,))
    sweep()
    assert not rows_of(admin, run)
    admin.execute("update app.cases set on_hold = false where id = %s", (case,))
    sweep()
    sweep()
    assert rows_of(admin, run) == [("bulk", "todo", WAKE_PRIORITY, f"case:{case}")]


@pytest.mark.parametrize("state", ["ocr_ready", "sort_ready", "finishing"])
def test_a_stranded_running_parent_is_woken_once_and_a_parent_with_a_row_is_not(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int], state: str
) -> None:
    stranded = run_with(admin, seed, case, state)
    sweep()
    sweep()
    assert len(rows_of(admin, stranded)) == 1
    busy_case = seed.case(seed.org(members={(p := seed.person()): "owner"}), p)
    busy = run_with(admin, seed, busy_case, state)
    add_row(admin, busy, busy_case, status="doing")
    sweep()
    assert len(rows_of(admin, busy)) == 1


# --- the mirror -------------------------------------------------------------------------------------------------


def test_a_failed_child_fails_the_parent_with_its_code(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case)
    kids(admin, seed, case, run, "running", "failed", code="job_error")
    sweep()
    assert job_state(admin, run)[:2] == ("failed", "job_error")
    assert run_state(admin, run) == "ocr_waiting"


def test_a_paused_child_pauses_the_parent_with_its_code(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case)
    kids(admin, seed, case, run, "running", "paused", code="cost_guard")
    sweep()
    assert job_state(admin, run)[:2] == ("paused", "cost_guard")


def test_a_child_failing_after_another_paused_takes_the_parent_paused_running_failed_in_one_go(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case, status="paused", code="service_busy")
    paused = child_job(admin, seed, case, status="paused", code="service_busy")
    failed = child_job(admin, seed, case, status="failed", code="job_error")
    add_batch(admin, run, case, 0, child=paused)
    add_batch(admin, run, case, 1, child=failed)
    add_row(admin, paused, case, lock=None, delay_seconds=3600)
    sweep()
    assert job_state(admin, run)[:2] == ("failed", "job_error")
    sweep()
    assert job_state(admin, run)[:2] == ("failed", "job_error")  # a later cleared pause does not resume it


def test_the_mirror_leaves_a_failed_parent_alone_check_2(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case, status="failed", code="job_error")
    kids(admin, seed, case, run, "failed", "paused", code="service_busy")
    sweep()
    assert job_state(admin, run)[:2] == ("failed", "job_error")


def test_cleared_pauses_put_the_mirrored_parent_back_to_running_without_an_attempt(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case, status="paused", code="service_busy")
    kids(admin, seed, case, run, "running", "running")
    attempts = job_state(admin, run)[2]
    sweep()
    assert job_state(admin, run)[:3] == ("running", None, attempts)
    assert not rows_of(admin, run)
    cancelled = run_with(admin, seed, case, status="paused", code="cancelled")
    sweep()
    assert job_state(admin, cancelled)[:2] == ("paused", "cancelled")  # a stop is not resumable


def test_a_resuming_child_changes_nothing(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case)
    kids(admin, seed, case, run, "paused", code="resuming")
    sweep()
    assert job_state(admin, run)[:2] == ("running", None)


def test_the_resume_race_the_mirror_waits_for_the_parents_row_and_for_the_reopen(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case)
    add_row(admin, run, case, status="doing")  # the resumed handler has not reopened the run yet
    kids(admin, seed, case, run, "failed", code="job_error")
    sweep()
    assert job_state(admin, run)[0] == "running"
    other = run_with(admin, seed, case)
    stale = kids(admin, seed, case, other, "paused", code="cost_guard")
    admin.execute("update app.pipeline_runs set reopened_at = now() where job_id = %s", (other,))
    sweep()
    assert job_state(admin, other)[0] == "running", stale  # a pause older than the reopen is ignored


def test_sweeps_over_an_unchanged_run_write_nothing_and_a_silent_parked_parent_is_touched_after_five_minutes(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case)
    kids(admin, seed, case, run, "running", "running")
    stamp = admin.execute("select updated_at from app.jobs where id = %s", (run,)).fetchone()
    for _ in range(3):
        sweep()
    assert admin.execute("select updated_at from app.jobs where id = %s", (run,)).fetchone() == stamp
    assert job_state(admin, run)[2] == 0
    admin.execute("set session_replication_role = replica")
    admin.execute("update app.jobs set updated_at = now() - interval '6 minutes' where id = %s", (run,))
    admin.execute("set session_replication_role = origin")
    sweep()
    row = admin.execute(
        "select updated_at > now() - interval '1 minute' from app.jobs where id = %s", (run,)
    ).fetchone()
    assert row == (True,)


# --- re-defer of sleepers ---------------------------------------------------------------------------------------


def test_a_paused_child_without_a_row_gets_one_with_no_lock_and_one_with_a_row_gets_none(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case)
    rowless = child_job(admin, seed, case, status="paused", code="service_busy")
    sleeping = child_job(admin, seed, case, status="paused", code="service_busy")
    add_batch(admin, run, case, 0, child=rowless)
    add_batch(admin, run, case, 1, child=sleeping)
    add_row(admin, rowless, case, lock=None, status="succeeded")
    add_row(admin, sleeping, case, lock=None, delay_seconds=3600)
    # the parent is `running` here only because the mirror runs first: it pauses the parent, children keep their codes
    sweep()
    sweep()
    assert rows_of(admin, rowless)[-1] == ("bulk", "todo", 0, None)
    assert len(rows_of(admin, rowless)) == 2
    assert len(rows_of(admin, sleeping)) == 1
    assert job_state(admin, rowless)[:2] == ("paused", "service_busy")


def test_a_parent_paused_in_its_own_tail_is_re_deferred_with_the_case_lock_once(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case, "finishing", status="paused", code="model_unavailable", priority=0)
    sweep()
    sweep()
    assert rows_of(admin, run) == [("bulk", "todo", 0, f"case:{case}")]
    assert job_state(admin, run)[:2] == ("paused", "model_unavailable")


def test_no_loop_against_the_back_off_a_mirrored_parent_stays_rowless_until_the_children_clear(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case)
    a, b = kids(admin, seed, case, run, "paused", "running", code="service_busy")
    admin.execute("update app.jobs set status = 'running', error_code = null where id = %s", (b,))
    add_row(admin, a, case, lock=None, delay_seconds=3600)
    for _ in range(4):
        sweep()
    assert job_state(admin, run)[:2] == ("paused", "service_busy")
    assert not rows_of(admin, run)
    assert len(rows_of(admin, a)) == 1
    admin.execute("update app.jobs set status = 'running', error_code = null where id = %s", (a,))
    sweep()
    assert job_state(admin, run)[0] == "running"
    assert not rows_of(admin, run)


def test_the_pass_order_is_mirror_then_stranded_wake(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case, "ocr_ready")
    kids(admin, seed, case, run, "failed", code="job_error")
    sweep()
    assert job_state(admin, run)[0] == "failed"  # the mirror ran first ...
    assert not rows_of(admin, run)  # ... so the wake saw a failed parent and did nothing


# --- waiters ----------------------------------------------------------------------------------------------------


def test_waiters_are_handed_back_once_when_the_case_is_no_longer_busy(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case, "sort_waiting", status="paused", code="cancelled")  # left open by a Stop
    waiter = make_job(admin, seed, case, kind="reprocess_case", status="paused", code="busy_processing")
    sweep()
    sweep()
    assert len(rows_of(admin, waiter)) == 1
    assert job_state(admin, run)[:2] == ("paused", "cancelled")


def test_a_waiter_stays_while_a_child_has_a_row_or_the_parent_is_paused_resumable_or_the_case_is_held(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case, "ocr_waiting", status="paused", code="cancelled")
    child = child_job(admin, seed, case, status="queued")
    add_batch(admin, run, case, 0, child=child)
    add_row(admin, child, case, lock=None)
    waiter = make_job(admin, seed, case, kind="reprocess_case", status="paused", code="busy_processing")
    sweep()
    assert not rows_of(admin, waiter)
    end_rows(admin, child)
    admin.execute("update app.cases set on_hold = true where id = %s", (case,))
    sweep()
    assert not rows_of(admin, waiter)
    admin.execute("update app.cases set on_hold = false where id = %s", (case,))
    sweep()
    assert len(rows_of(admin, waiter)) == 1


def test_children_paused_with_sleeping_rows_keep_the_waiter_waiting(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case)
    for child in kids(admin, seed, case, run, "paused", "paused", code="service_busy"):
        add_row(admin, child, case, lock=None, delay_seconds=3600)
    waiter = make_job(admin, seed, case, kind="reprocess_case", status="paused", code="busy_processing")
    sweep()
    sweep()
    assert not rows_of(admin, waiter)


# --- races ------------------------------------------------------------------------------------------------------


def race(worker_dsn: str, case: uuid.UUID, job: uuid.UUID, sweep: Callable[[], int]) -> None:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def resume() -> None:
        try:
            with psycopg.connect(worker_dsn, autocommit=True) as conn:
                conn.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
                barrier.wait(10)
                conn.execute("select app.resume_paused_job(%s, 'bulk')", (job,))
        except BaseException as exc:  # noqa: BLE001 - reported by the caller
            errors.append(exc)

    def heal() -> None:
        try:
            barrier.wait(10)
            sweep()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=resume), threading.Thread(target=heal)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors


def live_rows(admin: psycopg.Connection, job: uuid.UUID) -> int:
    return len([r for r in rows_of(admin, job) if r[1] in ("todo", "doing")])


def test_heal_racing_resume_paused_job_on_a_paused_child_leaves_one_row(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int], worker_dsn: str
) -> None:
    run = run_with(admin, seed, case)
    child = child_job(admin, seed, case, status="paused", code="service_busy")
    add_batch(admin, run, case, 0, child=child)
    add_row(admin, child, case, lock=None, status="succeeded")
    race(worker_dsn, case, child, sweep)
    assert live_rows(admin, child) == 1


def test_the_mirrors_wake_racing_resume_paused_job_on_the_parent_leaves_one_row_check_4(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int], worker_dsn: str
) -> None:
    run = run_with(admin, seed, case, status="paused", code="service_busy")
    kids(admin, seed, case, run, "done", "done")  # the last child finished while the parent was paused (F69)
    add_row(admin, run, case, status="succeeded")
    race(worker_dsn, case, run, sweep)
    assert live_rows(admin, run) == 1


def test_f69_the_last_child_completes_into_a_paused_parent_and_the_next_sweep_fires_exactly_once(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, sweep: Callable[[], int]
) -> None:
    run = run_with(admin, seed, case, status="paused", code="service_busy")
    kids(admin, seed, case, run, "done", "done")
    assert run_state(admin, run) == "ocr_waiting"
    sweep()
    sweep()
    assert run_state(admin, run) == "ocr_ready"
    assert job_state(admin, run)[0] == "running"
    assert live_rows(admin, run) == 1
