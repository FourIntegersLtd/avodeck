"""`app.requeue_orphaned_jobs` after migration 0138 (ADR 0038, F66): it leaves a parked parent alone, still requeues an
ordinary orphan, and puts a batch child back with no case lock."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.repository.test_pipeline_batches_support import (
    add_batch,
    add_row,
    add_run,
    child_job,
    job_state,
    make_job,
    rows_of,
)
from tests.support.seed import Seed


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(members={expert: "owner"}), expert)


def age(admin: psycopg.Connection, *jobs: uuid.UUID) -> None:
    """Make the jobs look untouched for two hours (the trigger would stamp now(), so it is skipped)."""
    admin.execute("set session_replication_role = replica")
    for job in jobs:
        admin.execute("update app.jobs set updated_at = now() - interval '2 hours' where id = %s", (job,))
    admin.execute("set session_replication_role = origin")


def sweep(admin: psycopg.Connection) -> None:
    admin.execute("select app.requeue_orphaned_jobs(3, 3600)")


def test_a_parked_parent_with_an_open_run_is_left_alone(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "ocr_waiting")
    age(admin, parent)
    sweep(admin)
    assert job_state(admin, parent)[0] == "running"
    assert not rows_of(admin, parent)


def test_an_ordinary_orphan_is_requeued_with_the_case_lock(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    orphan = make_job(admin, seed, case, kind="rerun_checks")
    age(admin, orphan)
    sweep(admin)
    assert job_state(admin, orphan)[0] == "queued"
    assert rows_of(admin, orphan)[-1][1:] == ("todo", 0, f"case:{case}")


def test_a_parent_whose_run_has_ended_is_an_ordinary_orphan(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "closed")
    age(admin, parent)
    sweep(admin)
    assert job_state(admin, parent)[0] == "queued"


def test_a_batch_child_that_lost_its_row_is_requeued_with_no_lock(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    run = add_run(admin, case, parent, "ocr_waiting")
    child = child_job(admin, seed, case, status="running")
    add_batch(admin, run, case, 0, child=child)
    add_row(admin, child, case, lock=None, status="succeeded")
    age(admin, child)
    sweep(admin)
    assert job_state(admin, child)[0] == "queued"
    assert rows_of(admin, child)[-1] == ("bulk", "todo", 0, None)
