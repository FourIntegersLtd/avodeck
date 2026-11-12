"""Slice A, audit 15 (BG-04, BG-07): "Try again" is bounded and checks the action of the job's own kind.

- BG-07: a person's retries of one failed job are counted for the job's whole life; past 5 the function answers
  False and says so through `app.pipeline_retry_limited`, so the route can answer 409 `retry_limit`. Another job
  keeps its own count.
- BG-04: only the processing kinds the route puts back can be retried, and the person needs the action the kind
  is registered under in `app.job_kind_actions` (here a kind the expert may run, but the route does not retry).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from tests.support.seed import Seed

CAP = 5


@contextmanager
def api_as(dsn: str, person: uuid.UUID) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=20)
    try:
        conn.execute("select set_config('app.person_id', %s, false)", (str(person),))
        yield conn
    finally:
        conn.close()


def _fail(admin: psycopg.Connection, job: uuid.UUID) -> None:
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'failed', error_code = 'job_error', attempts = 3 where id = %s", (job,))


def _retry(api: psycopg.Connection, admin: psycopg.Connection, job: uuid.UUID) -> bool:
    _fail(admin, job)
    row = api.execute("select app.requeue_failed_job(%s, 'zz_q', 0, false)", (job,)).fetchone()
    assert row is not None
    return bool(row[0])


def _limited(api: psycopg.Connection, job: uuid.UUID) -> bool:
    row = api.execute("select app.pipeline_retry_limited(%s)", (job,)).fetchone()
    assert row is not None
    return bool(row[0])


def test_the_sixth_person_retry_of_one_job_is_refused_and_flagged(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job, other = seed.job(case, requested_by=expert), seed.job(case, requested_by=expert)
    with api_as(api_dsn, expert) as api:
        assert [_retry(api, admin, job) for _ in range(CAP)] == [True] * CAP
        assert _limited(api, other) is False
        _fail(admin, job)
        assert _limited(api, job) is True
        assert api.execute("select app.requeue_failed_job(%s, 'zz_q', 0, false)", (job,)).fetchone() == (False,)
        assert admin.execute("select status from app.jobs where id = %s", (job,)).fetchone() == ("failed",)
        assert _retry(api, admin, other) is True, "another job's retries are counted on their own"


def test_a_person_without_access_cannot_read_the_limit_flag(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, stranger = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = seed.job(case, requested_by=expert)
    with api_as(api_dsn, expert) as api:
        for _ in range(CAP):
            _retry(api, admin, job)
        _fail(admin, job)
    with api_as(api_dsn, stranger) as api:
        assert _limited(api, job) is False


def test_a_kind_the_route_does_not_retry_is_refused_even_for_a_person_who_may_run_it(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    export = seed.job(case, kind="export_case", requested_by=expert)
    with api_as(api_dsn, expert) as api:
        assert _retry(api, admin, export) is False
    assert admin.execute("select status from app.jobs where id = %s", (export,)).fetchone() == ("failed",)


def _queue_rows(admin: psycopg.Connection, job: uuid.UUID) -> list[tuple[str, int]]:
    return admin.execute(
        "select queue_name, priority from procrastinate.procrastinate_jobs where args->>'job_id' = %s", (str(job),)
    ).fetchall()


def test_a_retry_cannot_choose_its_own_priority_or_the_other_kinds_queue(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    """BG-09 on the second path: the retry function holds a job to 0 or 10 and to its kind's queue, like enqueue_job."""
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = seed.job(case, requested_by=expert)  # process_bundle: not interactive, priority 0, bulk queue
    _fail(admin, job)
    with api_as(api_dsn, expert) as api:
        for queue, priority in (("zz_q", 1000000), ("zz_q", -5), ("zz_interactive", 0), ("zz_bulk", 10)):
            try:
                api.execute("select app.requeue_failed_job(%s, %s, %s, false)", (job, queue, priority))
            except psycopg.errors.InvalidParameterValue:
                pass
            assert _queue_rows(admin, job) == [], f"a retry at {queue}/{priority} reached the queue"
            assert admin.execute("select status from app.jobs where id = %s", (job,)).fetchone() == ("failed",)
        assert api.execute("select app.requeue_failed_job(%s, 'zz_bulk', 0, false)", (job,)).fetchone() == (True,)
    assert _queue_rows(admin, job) == [("zz_bulk", 0)]
