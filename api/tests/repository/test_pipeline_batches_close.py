"""`app.pipeline_close_runs` (migration 0137, ADR 0038, F60, F61): a new plan takes over only the runs that are not
healthy, after running the sweep's mirror on each, and leaves healthy runs open."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from tests.repository.test_pipeline_batches_support import (
    add_batch,
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


def close(admin: psycopg.Connection, case: uuid.UUID, by: uuid.UUID) -> int:
    admin.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
    row = admin.execute("select app.pipeline_close_runs(%s, %s, %s)", (case, by, RESUMABLE)).fetchone()
    assert row is not None
    return int(row[0])


def stop(admin: psycopg.Connection, case: uuid.UUID) -> None:
    admin.execute(
        "insert into app.case_processing (case_id, cancel_requested_at) values (%s, now())"
        " on conflict (case_id) do update set cancel_requested_at = now()",
        (case,),
    )


def open_run(admin: psycopg.Connection, seed: Seed, case: uuid.UUID, **kw: Any) -> uuid.UUID:
    return add_run(admin, case, make_job(admin, seed, case, **kw), "ocr_waiting")


def test_only_the_worker_may_call_close_runs(admin: psycopg.Connection) -> None:
    row = admin.execute(
        "select has_function_privilege('app_worker', 'app.pipeline_close_runs(uuid, uuid, text[])', 'execute'),"
        " has_function_privilege('app_api', 'app.pipeline_close_runs(uuid, uuid, text[])', 'execute')"
    ).fetchone()
    assert row == (True, False)


@pytest.mark.parametrize(("status", "code"), [("paused", "cancelled"), ("failed", "job_error")])
def test_a_run_whose_parent_is_paused_or_failed_is_closed_by_the_new_job(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, status: str, code: str
) -> None:
    old = open_run(admin, seed, case, status=status, code=code)
    new = make_job(admin, seed, case)
    assert close(admin, case, new) == 1
    row = admin.execute(
        "select state, closed_by_job_id, closed_at is not null from app.pipeline_runs where job_id = %s", (old,)
    ).fetchone()
    assert row == ("closed", new, True)


def test_a_healthy_run_stays_open_and_a_finished_one_is_not_touched(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    healthy = open_run(admin, seed, case)
    finished = add_run(admin, case, make_job(admin, seed, case, status="done"), "done")
    assert close(admin, case, make_job(admin, seed, case)) == 0
    assert (run_state(admin, healthy), run_state(admin, finished)) == ("ocr_waiting", "done")


def test_a_stop_asked_after_the_parent_was_created_closes_a_running_run_and_pauses_its_parent(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    old = open_run(admin, seed, case)
    stop(admin, case)
    assert close(admin, case, make_job(admin, seed, case)) == 1
    assert run_state(admin, old) == "closed"
    assert job_state(admin, old)[:2] == ("paused", "cancelled")


def test_a_stop_asked_before_the_parent_was_created_or_before_the_reopen_is_ignored(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    stop(admin, case)
    fresh = open_run(admin, seed, case)  # created after the stop
    reopened = open_run(admin, seed, case)
    admin.execute("update app.pipeline_runs set reopened_at = now() where job_id = %s", (reopened,))
    assert close(admin, case, make_job(admin, seed, case)) == 0
    assert (run_state(admin, fresh), run_state(admin, reopened)) == ("ocr_waiting", "ocr_waiting")


def test_the_mirror_runs_first_so_a_failed_child_closes_the_run_before_the_sweep_has_written_it(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    old = open_run(admin, seed, case)
    child = child_job(admin, seed, case, status="failed", code="job_error")
    add_batch(admin, old, case, 0, child=child)
    assert close(admin, case, make_job(admin, seed, case)) == 1
    assert run_state(admin, old) == "closed"
    assert job_state(admin, old)[0] == "failed"


def test_the_new_jobs_own_run_and_other_cases_are_not_touched(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    other_owner = seed.person()
    other_case = seed.case(seed.org(members={other_owner: "owner"}), other_owner)
    elsewhere = open_run(admin, seed, other_case, status="paused", code="cancelled")
    new = make_job(admin, seed, case, status="paused", code="cancelled")
    own = add_run(admin, case, new, "ocr_waiting")
    assert close(admin, case, new) == 0
    assert (run_state(admin, elsewhere), run_state(admin, own)) == ("ocr_waiting", "ocr_waiting")
    assert not rows_of(admin, new)
