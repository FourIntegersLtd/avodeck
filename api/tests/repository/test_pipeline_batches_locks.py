"""Lock order and run order of the page-batch functions (migration 0137, ADR 0038, F57, F61, F62, F64).

Every function that works on a case's runs takes ALL the case's open run rows, oldest first, before it does anything
else, so two functions never wait on each other's run rows (no `40P01`). Each deadlock test makes the interleaving
certain: a holder connection keeps the older run's row, the two contenders queue behind it in a known order, and the
holder lets go. Ids, counts and codes only (rule 6).
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import psycopg
import pytest

from tests.repository.test_pipeline_batches_support import (
    add_batch,
    add_run,
    child_job,
    make_job,
    run_state,
)
from tests.support.seed import Seed

RESUMABLE = ["service_busy", "model_unavailable"]


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(members={expert: "owner"}), expert)


def two_runs(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """(parent, run, batch) of an older and a younger run of one case, each with one open batch."""
    made = []
    for _ in range(2):
        parent = make_job(admin, seed, case)
        run = add_run(admin, case, parent, "ocr_waiting")
        child = child_job(admin, seed, case, status="running")
        made.append((parent, run, add_batch(admin, run, case, 0, child=child)))
        time.sleep(0.01)  # distinct creation times: the order of the runs is the order of these rows
    return made


def wait_blocked(admin: psycopg.Connection, name: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = admin.execute(
            "select 1 from pg_stat_activity where application_name = %s and wait_event_type = 'Lock'", (name,)
        ).fetchone()
        if row is not None:
            return
        time.sleep(0.05)
    raise AssertionError("a contender never started waiting")


class Contender:
    """One statement run on its own connection and thread; its outcome is kept for the test to read."""

    def __init__(
        self,
        dsn: str,
        case: uuid.UUID,
        sql: str,
        params: tuple[Any, ...],
        setup: tuple[str, tuple[Any, ...]] | None = None,
    ) -> None:
        self.setup = setup
        self.name = f"zz_lock_{uuid.uuid4().hex[:12]}"
        self.error: BaseException | None = None
        self.result: Any = None
        self.done = False
        self._thread = threading.Thread(target=self._run, args=(dsn, case, sql, params))

    def _run(self, dsn: str, case: uuid.UUID, sql: str, params: tuple[Any, ...]) -> None:
        try:
            with psycopg.connect(dsn, autocommit=True, application_name=self.name) as conn:
                conn.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
                if self.setup is not None:
                    conn.execute(*self.setup)
                row = conn.execute(sql, params).fetchone()
                self.result = None if row is None else row[0]
                self.done = True
        except BaseException as exc:  # noqa: BLE001 - kept for the test
            self.error = exc

    def start(self, admin: psycopg.Connection) -> None:
        self._thread.start()
        wait_blocked(admin, self.name)

    def finish(self) -> None:
        self._thread.join(30)
        assert not self._thread.is_alive(), "a contender is stuck"


COMPLETE = "select app.pipeline_complete_batch(%s)"
CLOSE = "select app.pipeline_close_runs(%s, %s, %s)"


def hold(admin_dsn: str, run: uuid.UUID) -> psycopg.Connection:
    holder = psycopg.connect(admin_dsn, autocommit=False)
    holder.execute("select 1 from app.pipeline_runs where job_id = %s for update", (run,))
    return holder


def test_two_runs_of_a_case_completing_at_once_do_not_deadlock(
    admin: psycopg.Connection, admin_dsn: str, seed: Seed, case: uuid.UUID
) -> None:
    (_, older, older_batch), (_, younger, younger_batch) = two_runs(admin, seed, case)
    holder = hold(admin_dsn, older)
    first = Contender(admin_dsn, case, COMPLETE, (older_batch,))
    second = Contender(admin_dsn, case, COMPLETE, (younger_batch,))
    try:
        first.start(admin)
        second.start(admin)
    finally:
        holder.rollback()
        holder.close()
    first.finish()
    second.finish()
    assert first.error is None, type(first.error).__name__
    assert second.error is None, type(second.error).__name__
    assert run_state(admin, older) == "ocr_ready"
    assert run_state(admin, younger) == "ocr_waiting" or run_state(admin, younger) == "ocr_ready"


def test_closing_runs_and_a_childs_completion_do_not_deadlock(
    admin: psycopg.Connection, admin_dsn: str, seed: Seed, case: uuid.UUID
) -> None:
    (_, older, _), (_, younger, younger_batch) = two_runs(admin, seed, case)
    new_parent = make_job(admin, seed, case)
    holder = hold(admin_dsn, older)
    closer = Contender(admin_dsn, case, CLOSE, (case, new_parent, RESUMABLE))
    completer = Contender(admin_dsn, case, COMPLETE, (younger_batch,))
    try:
        closer.start(admin)
        completer.start(admin)
    finally:
        holder.rollback()
        holder.close()
    closer.finish()
    completer.finish()
    assert closer.error is None, type(closer.error).__name__
    assert completer.error is None, type(completer.error).__name__
    assert run_state(admin, younger) in ("ocr_waiting", "ocr_ready", "closed")


def test_a_completion_that_finds_its_run_gone_returns_false_not_null(
    admin: psycopg.Connection, admin_dsn: str, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    run = add_run(admin, case, parent, "ocr_waiting")
    batch = add_batch(admin, run, case, 0, child=child_job(admin, seed, case, status="running"))
    holder = hold(admin_dsn, run)
    completer = Contender(admin_dsn, case, COMPLETE, (batch,))
    try:
        completer.start(admin)
        holder.execute("delete from app.pipeline_runs where job_id = %s", (run,))
        holder.commit()
    finally:
        holder.close()
    completer.finish()
    assert completer.error is None, type(completer.error).__name__
    assert completer.done
    assert completer.result is False


def test_a_purge_racing_the_last_childs_completion_ends_clean(
    admin: psycopg.Connection, admin_dsn: str, seed: Seed, case: uuid.UUID
) -> None:
    """The purge deletes the batches (its trigger takes the run rows first), then the runs; a completion that gets
    in between waits for the purge and finds its run gone: no `40P01`, no row left, a quiet false."""
    parent = make_job(admin, seed, case)
    run = add_run(admin, case, parent, "ocr_waiting")
    batch = add_batch(admin, run, case, 0, child=child_job(admin, seed, case, status="running"))
    purge = psycopg.connect(admin_dsn, autocommit=False)
    purge.execute("select set_config('app.purging_case_id', %s, true)", (str(case),))
    purge.execute("delete from app.pipeline_batches where case_id = %s", (case,))
    completer = Contender(admin_dsn, case, COMPLETE, (batch,))
    try:
        completer.start(admin)
        purge.execute("delete from app.pipeline_runs where case_id = %s", (case,))
        purge.commit()
    finally:
        purge.close()
    completer.finish()
    assert completer.error is None, type(completer.error).__name__
    assert completer.result is False
    left = admin.execute("select count(*) from app.pipeline_runs where case_id = %s", (case,)).fetchone()
    assert left == (0,)


def enter(admin: psycopg.Connection, case: uuid.UUID, run: uuid.UUID) -> str:
    admin.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
    row = admin.execute("select app.pipeline_enter(%s)", (run,)).fetchone()
    assert row is not None
    return str(row[0])


def test_a_younger_run_with_nothing_open_waits_for_an_older_run_with_a_running_parent_f61(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    """`pipeline_enter` follows the same order rule as the fan-in: it does not move a younger run on while an older
    run of the case still has a running parent."""
    older_parent = make_job(admin, seed, case)
    older = add_run(admin, case, older_parent, "ocr_waiting")
    add_batch(admin, older, case, 0, child=child_job(admin, seed, case, status="running"))
    time.sleep(0.01)
    younger_parent = make_job(admin, seed, case)
    younger = add_run(admin, case, younger_parent, "ocr_waiting")
    add_batch(admin, younger, case, 0, child=child_job(admin, seed, case, status="done"), done=True)
    assert enter(admin, case, younger) == "park"
    assert run_state(admin, younger) == "ocr_waiting"
    admin.execute("update app.pipeline_runs set state = 'done' where job_id = %s", (older,))
    assert enter(admin, case, younger) == "wake_a"
    assert run_state(admin, younger) == "ocr_ready"


# --- the purge and the fan-in take the case's run rows in ONE order (review of #284) -------------------------------

PURGE = "with d as (delete from app.pipeline_batches where case_id = %s returning 1) select count(*) from d"


def runs_against_uuid_order(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, second_state: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """(older run, its batch, younger run, its batch): the older run has the LARGER uuid, so an order by uuid and an
    order by age disagree. The younger run is `second_state` (open, or closed)."""
    first, second = sorted((make_job(admin, seed, case), make_job(admin, seed, case)))
    older = add_run(admin, case, second, "ocr_waiting")
    older_batch = add_batch(admin, older, case, 0, child=child_job(admin, seed, case, status="running"))
    time.sleep(0.01)
    younger = add_run(admin, case, first, second_state)
    younger_batch = add_batch(admin, younger, case, 0, child=child_job(admin, seed, case, status="running"))
    return older, older_batch, younger, younger_batch


@pytest.mark.parametrize("second_state", ["ocr_waiting", "closed"])
def test_the_purge_and_a_completion_take_the_run_rows_in_the_same_order(
    admin: psycopg.Connection, admin_dsn: str, seed: Seed, case: uuid.UUID, second_state: str
) -> None:
    """Runs of one case whose uuid order is the reverse of their age order, the younger one open or closed: the
    completion (of the younger run's batch) and the purge's batch delete must not each hold one and want the other."""
    older, _, _, younger_batch = runs_against_uuid_order(admin, seed, case, second_state)
    holder = hold(admin_dsn, older)
    completer = Contender(admin_dsn, case, COMPLETE, (younger_batch,))
    purger = Contender(
        admin_dsn, case, PURGE, (case,), setup=("select set_config('app.purging_case_id', %s, false)", (str(case),))
    )
    try:
        completer.start(admin)  # first in line for the older run's row, holding nothing
        purger.start(admin)  # takes whatever it can reach, then waits behind the completer
    finally:
        holder.rollback()
        holder.close()
    completer.finish()
    purger.finish()
    assert completer.error is None, type(completer.error).__name__
    assert purger.error is None, type(purger.error).__name__


# --- the sweep lets go of what it could lock when it skips a case (review of #284) ----------------------------------


def unlocked(admin_dsn: str, run: uuid.UUID) -> bool:
    with psycopg.connect(admin_dsn, autocommit=True) as probe:
        row = probe.execute(
            "select job_id from app.pipeline_runs where job_id = %s for update skip locked", (run,)
        ).fetchone()
    return row is not None


def test_a_skipped_case_leaves_none_of_its_run_rows_locked_by_the_sweep(
    admin: psycopg.Connection, admin_dsn: str, seed: Seed, case: uuid.UUID
) -> None:
    """The older run is held by a completion, so the sweep skips the case; the younger run's row, which it could
    lock, must not stay locked until the sweep's transaction ends (that would stall a completion or a close). A
    paused waiter makes the second pass (the waiters) look at the case too."""
    (_, older, _), (_, younger, _) = two_runs(admin, seed, case)
    make_job(admin, seed, case, kind="reprocess_case", status="paused", code="busy_processing")
    holder = hold(admin_dsn, older)
    sweeper = psycopg.connect(admin_dsn, autocommit=False)
    try:
        sweeper.execute("select app.pipeline_heal_runs(%s)", (RESUMABLE,))
        assert unlocked(admin_dsn, younger), "the sweep kept the younger run's row after skipping the case"
    finally:
        sweeper.rollback()
        sweeper.close()
        holder.rollback()
        holder.close()
