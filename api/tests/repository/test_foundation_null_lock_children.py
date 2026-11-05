"""A page-batch child is put back with no case lock (migration 9003; ADR 0038, F66).

`app.resume_paused_job()` and `app.support_job_action()` defer a new queue row with `case:<id>` when the job's
earlier rows carry no lock. A batch child (a `pipeline_batches` row names it as `child_job_id`) must never take the
case lock, or one child would block the case. The rule is inert while the table does not exist: these tests stand a
minimal `app.pipeline_batches` in for it. The behaviours 9001 and 0064 fixed stay: the waiting row is found by the job
on whatever queue it waits, and a job is never queued twice.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from app.core import jobs
from app.core.db import Database
from tests.repository.test_foundation_job_queue_resume import paused_job_waiting_on, queue_rows
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def batches_table(admin: psycopg.Connection) -> Iterator[None]:
    """A stand-in for the pipeline's table, owned like a migration would own it; dropped again afterwards."""
    exists = admin.execute("select to_regclass('app.pipeline_batches')").fetchone() == (None,)
    if exists:
        admin.execute("create table app.pipeline_batches (id bigserial primary key, child_job_id uuid)")
        admin.execute("alter table app.pipeline_batches owner to app_migrator")
    try:
        yield
    finally:
        if exists:
            admin.execute("drop table app.pipeline_batches")


def make_child(admin: psycopg.Connection, job: uuid.UUID) -> None:
    admin.execute("insert into app.pipeline_batches (child_job_id) values (%s)", (job,))


@pytest.fixture
def people(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    expert, staff = seed.person(), seed.person()
    seed.support_staff(staff)
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    return expert, staff, case


def lock_of(admin: psycopg.Connection, job: uuid.UUID) -> list[str | None]:
    rows = admin.execute(
        "select lock from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s order by id", (str(job),)
    ).fetchall()
    return [r[0] for r in rows]


def paused_without_row(admin: psycopg.Connection, seed: Seed, case: uuid.UUID, code: str = "cost_guard") -> uuid.UUID:
    job = seed.job(case, kind="zz_paused")
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'paused', error_code = %s where id = %s", (code, job))
    return job


def failed_without_row(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> uuid.UUID:
    job = seed.job(case, kind="zz_failed")
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'failed', error_code = 'job_error' where id = %s", (job,))
    return job


async def test_try_again_puts_a_child_back_with_no_lock(
    api_db: Database, admin: psycopg.Connection, seed: Seed, batches_table: None,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    expert, _, case = people
    job = paused_without_row(admin, seed, case)
    make_child(admin, job)
    async with api_db.request(scope_for(expert)) as conn:
        assert await (await conn.execute("select app.resume_paused_job(%s, 'bulk')", (job,))).fetchone() == (True,)
    assert lock_of(admin, job) == [None]
    assert [(q, s) for q, s, _ in queue_rows(admin, job)] == [("bulk", "todo")]


async def test_try_again_still_gives_any_other_job_the_case_lock(
    api_db: Database, admin: psycopg.Connection, seed: Seed, batches_table: None,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    expert, _, case = people
    job = paused_without_row(admin, seed, case)
    make_child(admin, seed.job(case, kind="zz_other_child"))  # someone else's child: no effect on this job
    async with api_db.request(scope_for(expert)) as conn:
        assert await (await conn.execute("select app.resume_paused_job(%s, 'jobs')", (job,))).fetchone() == (True,)
    assert lock_of(admin, job) == [f"case:{case}"]


async def test_the_rule_does_nothing_while_the_table_does_not_exist(
    api_db: Database, admin: psycopg.Connection, seed: Seed, people: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    if admin.execute("select to_regclass('app.pipeline_batches')").fetchone() != (None,):
        pytest.skip("the pipeline's table exists: covered by the tests above")
    expert, staff, case = people
    job = paused_without_row(admin, seed, case)
    async with api_db.request(scope_for(expert)) as conn:
        assert await (await conn.execute("select app.resume_paused_job(%s, 'jobs')", (job,))).fetchone() == (True,)
    failed = failed_without_row(admin, seed, case)
    async with api_db.request(scope_for(staff)) as conn:
        done = await jobs.support_job_action(conn, failed, "requeue", "fault_fixed")
    assert done.outcome == "ok"
    assert lock_of(admin, job) == [f"case:{case}"] and lock_of(admin, failed) == [f"case:{case}"]


async def test_support_requeue_of_a_failed_child_has_no_lock_and_of_another_job_has_one(
    api_db: Database, admin: psycopg.Connection, seed: Seed, batches_table: None,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    _, staff, case = people
    child, other = failed_without_row(admin, seed, case), failed_without_row(admin, seed, case)
    make_child(admin, child)
    async with api_db.request(scope_for(staff)) as conn:
        assert (await jobs.support_job_action(conn, child, "requeue", "fault_fixed")).outcome == "ok"
        assert (await jobs.support_job_action(conn, other, "requeue", "fault_fixed")).outcome == "ok"
    assert lock_of(admin, child) == [None]
    assert lock_of(admin, other) == [f"case:{case}"]


async def test_support_resume_of_a_paused_child_has_no_lock(
    api_db: Database, admin: psycopg.Connection, seed: Seed, batches_table: None,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    _, staff, case = people
    job = paused_without_row(admin, seed, case)
    make_child(admin, job)
    async with api_db.request(scope_for(staff)) as conn:
        assert (await jobs.support_job_action(conn, job, "resume", "service_restored")).outcome == "ok"
    assert lock_of(admin, job) == [None]


@pytest.mark.parametrize("queue", ["bulk", "interactive", "jobs"])
async def test_a_child_waiting_on_any_queue_is_still_found_by_the_job_and_not_doubled(
    queue: str, api_db: Database, admin: psycopg.Connection, seed: Seed, batches_table: None,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    """The 9001 and 0064 behaviour survives: one waiting row, on its own queue, brought forward."""
    expert, staff, case = people
    job = paused_job_waiting_on(admin, seed, case, queue)
    make_child(admin, job)
    async with api_db.request(scope_for(expert)) as conn:
        assert await (await conn.execute("select app.resume_paused_job(%s, 'jobs')", (job,))).fetchone() == (True,)
    assert queue_rows(admin, job) == [(queue, "todo", True)]
    other = paused_job_waiting_on(admin, seed, case, queue)
    make_child(admin, other)
    async with api_db.request(scope_for(staff)) as conn:
        assert (await jobs.support_job_action(conn, other, "resume", "service_restored")).outcome == "ok"
    assert queue_rows(admin, other) == [(queue, "todo", True)]


def test_two_hand_backs_at_once_leave_one_queue_row(
    api_dsn: str, admin: psycopg.Connection, seed: Seed, batches_table: None,
    people: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    """Barrier: the hand-back takes the job row `for update`, so a second caller (the pipeline's heal and mirror use
    the same rule) waits, then finds `resuming` with a row and does nothing: never two rows, never two runs."""
    expert, _, case = people
    job = paused_without_row(admin, seed, case)
    make_child(admin, job)
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def call() -> None:
        with psycopg.connect(api_dsn, connect_timeout=20) as conn:
            with conn.transaction():
                conn.execute("select set_config('app.person_id', %s, true)", (str(expert),))
                barrier.wait(timeout=20)
                row = conn.execute("select app.resume_paused_job(%s, 'bulk')", (job,)).fetchone()
                assert row is not None
                results.append(bool(row[0]))

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(results) == [False, True]
    assert [(q, s) for q, s, _ in queue_rows(admin, job)] == [("bulk", "todo")]
    assert lock_of(admin, job) == [None]
