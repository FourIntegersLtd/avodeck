"""Background jobs, audit 15, BG-11 (ordering): a case's queued work waits behind other cases' work, within bounds.

A new queue row's priority is lowered by how many rows the same case already has queued or running on the same queue,
at most 9 (so an interactive row, base 10, never falls to the bulk base 0), for the two base priorities only. A worker
inside its own job (a page batch's children, the heal sweep) is not lowered: they are the same case by design.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from tests.support.background_jobs import defer
from tests.support.seed import Seed


def _case(seed: Seed) -> uuid.UUID:
    person = seed.person()
    return seed.case(seed.org(kind="personal", members={person: "owner"}), person)


def _queued(admin: psycopg.Connection, case: uuid.UUID, queue: str, n: int, *, priority: int = 0) -> list[int]:
    """Defer `n` rows for one case on one queue, as the queue function does, and return the priorities they got."""
    out: list[int] = []
    for _ in range(n):
        job = uuid.uuid4()
        defer(admin, queue, job, case, priority=priority)
        row = admin.execute(
            "select priority from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(job),)
        ).fetchone()
        assert row is not None
        out.append(row[0])
    return out


@contextmanager
def _inside_a_job_of(admin: psycopg.Connection, case: uuid.UUID) -> Iterator[None]:
    admin.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
    try:
        yield
    finally:
        admin.execute("select set_config('app.job_case_id', '', false)")


def test_each_new_row_of_a_case_waits_one_step_behind_the_rows_it_already_has(
    admin: psycopg.Connection, seed: Seed
) -> None:
    assert _queued(admin, _case(seed), "zz_fair_bulk", 4) == [0, -1, -2, -3]


def test_the_lowering_stops_at_nine_so_the_priority_stays_bounded(admin: psycopg.Connection, seed: Seed) -> None:
    got = _queued(admin, _case(seed), "zz_fair_bulk", 14)
    assert min(got) == -9 and got[-1] == -9


def test_an_interactive_row_never_falls_to_the_bulk_base(admin: psycopg.Connection, seed: Seed) -> None:
    got = _queued(admin, _case(seed), "zz_fair_interactive", 14, priority=10)
    assert got[0] == 10 and min(got) >= 1


def test_another_cases_first_row_is_not_lowered_by_this_cases_backlog(admin: psycopg.Connection, seed: Seed) -> None:
    _queued(admin, _case(seed), "zz_fair_bulk", 8)
    assert _queued(admin, _case(seed), "zz_fair_bulk", 1) == [0]


def test_bulk_work_does_not_lower_the_same_cases_interactive_work(admin: psycopg.Connection, seed: Seed) -> None:
    case = _case(seed)
    _queued(admin, case, "zz_fair_bulk", 8)
    assert _queued(admin, case, "zz_fair_interactive", 1, priority=10) == [10]


def test_a_worker_inside_its_own_jobs_case_is_not_lowered(admin: psycopg.Connection, seed: Seed) -> None:
    case = _case(seed)
    _queued(admin, case, "zz_fair_bulk", 6)
    with _inside_a_job_of(admin, case):
        assert _queued(admin, case, "zz_fair_bulk", 3) == [0, 0, 0]


def test_a_worker_inside_another_cases_job_is_still_lowered(admin: psycopg.Connection, seed: Seed) -> None:
    case = _case(seed)
    _queued(admin, case, "zz_fair_bulk", 2)
    with _inside_a_job_of(admin, _case(seed)):
        assert _queued(admin, case, "zz_fair_bulk", 1) == [-2]


def test_putting_an_existing_job_back_on_the_queue_is_not_new_backlog(admin: psycopg.Connection, seed: Seed) -> None:
    """A job that already had a queue row (the heal sweep's hand-back, a wake, a retry) keeps its priority: it is the
    same work again, and lowering it would let other cases overtake a bundle that is already under way."""
    case = _case(seed)
    job = uuid.uuid4()
    defer(admin, "zz_fair_bulk", job, case)
    _queued(admin, case, "zz_fair_bulk", 4)
    admin.execute("set search_path = procrastinate, public")  # the queue's own status trigger needs it
    try:
        admin.execute(
            "update procrastinate.procrastinate_jobs set status = 'succeeded' where args ->> 'job_id' = %s",
            (str(job),),
        )
    finally:
        admin.execute("reset search_path")
    defer(admin, "zz_fair_bulk", job, case)
    got = admin.execute(
        "select priority from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s order by id", (str(job),)
    ).fetchall()
    assert got == [(0,), (0,)]


def test_a_special_priority_is_left_as_it_was_given(admin: psycopg.Connection, seed: Seed) -> None:
    """The parent's wake (priority 20) and any other non-base value are the queue code's own choice: untouched."""
    case = _case(seed)
    _queued(admin, case, "zz_fair_bulk", 5)
    assert _queued(admin, case, "zz_fair_bulk", 1, priority=20) == [20]


def test_finished_rows_do_not_count(admin: psycopg.Connection, seed: Seed) -> None:
    case = _case(seed)
    _queued(admin, case, "zz_fair_bulk", 5)
    admin.execute("set search_path = procrastinate, public")  # the queue's own status trigger needs it
    try:
        admin.execute(
            "update procrastinate.procrastinate_jobs set status = 'succeeded' where args ->> 'case_id' = %s",
            (str(case),),
        )
    finally:
        admin.execute("reset search_path")
    assert _queued(admin, case, "zz_fair_bulk", 1) == [0]


def test_the_stored_job_keeps_its_base_priority_while_the_queue_row_is_lowered(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    """BG-09's pin is on `app.jobs.priority`; the ordering is only on the queue row."""
    person = seed.person()
    case = seed.case(seed.org(kind="personal", members={person: "owner"}), person)
    conn = psycopg.connect(api_dsn, autocommit=True, connect_timeout=20)
    try:
        conn.execute("select set_config('app.person_id', %s, false)", (str(person),))
        ids = [
            conn.execute("select app.enqueue_job(%s, 'zz_fair', 'zz_fair_bulk', 0, false, 'x')", (case,)).fetchone()
            for _ in range(3)
        ]
    finally:
        conn.close()
    stored = [admin.execute("select priority from app.jobs where id = %s", (r[0],)).fetchone() for r in ids if r]
    queued = [
        admin.execute(
            "select priority from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(r[0]),)
        ).fetchone()
        for r in ids
        if r
    ]
    assert stored == [(0,), (0,), (0,)]
    assert queued == [(0,), (-1,), (-2,)]


def test_the_ordering_index_exists(admin: psycopg.Connection) -> None:
    row = admin.execute(
        "select 1 from pg_indexes where schemaname = 'procrastinate' and tablename = 'procrastinate_jobs'"
        " and indexdef ilike '%%case_id%%'"
    ).fetchone()
    assert row is not None
