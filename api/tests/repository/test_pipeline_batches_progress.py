"""`app.pipeline_start_phase` and `app.pipeline_report_progress` (migration 0142, ADR 0038, build step 10).

A phase's children report on the parent's row: the step, the bar and the counts come from live page counts in one
statement, the bar only rises, the step never goes back, and a page set aside after the plan cannot hold the bar short.
Counts and step codes only (rule 6).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.repository.test_pipeline_batches_support import add_batch, add_row, add_run, child_job, make_job, rows_of
from tests.support.seed import Seed


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(members={expert: "owner"}), expert)


def as_job_on(conn: psycopg.Connection, case: uuid.UUID) -> None:
    conn.execute("select set_config('app.job_case_id', %s, false)", (str(case),))


def report(conn: psycopg.Connection, case: uuid.UUID, run: uuid.UUID) -> None:
    as_job_on(conn, case)
    conn.execute("select app.pipeline_report_progress(%s)", (run,))


def parent_row(admin: psycopg.Connection, parent: uuid.UUID) -> tuple[str | None, int | None, int | None, int | None]:
    row = admin.execute("select step, pct, step_done, step_total from app.jobs where id = %s", (parent,)).fetchone()
    assert row is not None
    return row


def pages(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, count: int, *, read: int, extracted: int
) -> list[uuid.UUID]:
    """`count` pages from 1 with a picture; the first `read` have words, the first `extracted` an extraction."""
    document = seed.document(case)
    ids = [seed.page(case, document, n) for n in range(1, count + 1)]
    for index, page in enumerate(ids):
        admin.execute(
            "update app.pages set image_path = 'p', text_source = case when %s then 'ocr' end where id = %s",
            (index < read, page),
        )
        if index < extracted:
            admin.execute(
                "insert into app.page_analysis (page_id, bundle_id, extracted_at)"
                " select id, bundle_id, now() from app.pages where id = %s",
                (page,),
            )
    return ids


def test_the_ocr_phase_reports_its_step_bar_and_counts(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "ocr_waiting")
    pages(admin, seed, case, 10, read=5, extracted=0)
    add_batch(admin, parent, case, 0, pages=10)
    report(admin, case, parent)
    assert parent_row(admin, parent) == ("ocr", 35, 5, 10)


def test_the_sort_phase_reports_from_the_extracted_pages(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "sort_waiting")
    pages(admin, seed, case, 10, read=10, extracted=4)
    add_batch(admin, parent, case, 0, phase="sort", pages=10)
    report(admin, case, parent)
    assert parent_row(admin, parent) == ("sort", 46, 4, 10)


def test_the_bar_only_rises_and_the_step_never_goes_back(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "ocr_waiting")
    pages(admin, seed, case, 10, read=2, extracted=0)
    add_batch(admin, parent, case, 0, pages=10)
    admin.execute("update app.jobs set step = 'sort', pct = 50, step_done = 6, step_total = 9 where id = %s", (parent,))
    report(admin, case, parent)
    assert parent_row(admin, parent) == ("sort", 50, 6, 9), "an older figure written late changed the parent's row"


def test_a_page_set_aside_after_the_plan_cannot_hold_the_bar_short(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "ocr_waiting")
    ids = pages(admin, seed, case, 10, read=9, extracted=0)
    add_batch(admin, parent, case, 0, pages=10)
    admin.execute("update app.pages set quarantined = true where id = %s", (ids[9],))
    report(admin, case, parent)
    assert parent_row(admin, parent) == ("ocr", 40, 9, 9)


def test_a_run_past_its_phases_leaves_the_parent_alone(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "finishing")
    pages(admin, seed, case, 4, read=4, extracted=4)
    add_batch(admin, parent, case, 0, pages=4)
    admin.execute("update app.jobs set step = 'missing', pct = 80 where id = %s", (parent,))
    report(admin, case, parent)
    assert parent_row(admin, parent) == ("missing", 80, None, None)


def test_report_for_another_case_is_refused(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "ocr_waiting")
    as_job_on(admin, uuid.uuid4())
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute("select app.pipeline_report_progress(%s)", (parent,))


def test_start_phase_queues_children_up_to_the_window(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case)
    add_row(admin, parent, case, status="doing")
    add_run(admin, case, parent, "ocr_waiting", window=2)
    for seq in range(4):
        add_batch(admin, parent, case, seq)
    as_job_on(admin, case)
    admin.execute("select app.pipeline_start_phase(%s)", (parent,))
    children = [
        r[0]
        for r in admin.execute(
            "select child_job_id from app.pipeline_batches where run_job_id = %s order by seq", (parent,)
        )
    ]
    assert [c is not None for c in children] == [True, True, False, False]
    for child in children[:2]:
        assert rows_of(admin, child)[0][1:] == ("todo", 0, None), "a child has a queue row with no case lock"


def test_start_phase_for_another_case_is_refused(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "ocr_waiting")
    as_job_on(admin, uuid.uuid4())
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute("select app.pipeline_start_phase(%s)", (parent,))


def test_only_the_worker_may_call_them(admin: psycopg.Connection) -> None:
    for name in ("pipeline_start_phase(uuid)", "pipeline_report_progress(uuid)"):
        row = admin.execute(
            "select has_function_privilege('app_worker', %s::regprocedure, 'execute'),"
            " has_function_privilege('app_api', %s::regprocedure, 'execute'),"
            " has_function_privilege('public', %s::regprocedure, 'execute')",
            (f"app.{name}",) * 3,
        ).fetchone()
        assert row == (True, False, False), name


def test_a_finished_child_does_not_take_a_window_slot(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    """The window counts children not yet completed: a batch whose child finished does not take a slot."""
    parent = make_job(admin, seed, case)
    add_row(admin, parent, case, status="doing")
    add_run(admin, case, parent, "ocr_waiting", window=2)
    add_batch(admin, parent, case, 0, child=child_job(admin, seed, case, status="done"), done=True)
    for seq in (1, 2, 3):
        add_batch(admin, parent, case, seq)
    as_job_on(admin, case)
    admin.execute("select app.pipeline_start_phase(%s)", (parent,))
    queued = admin.execute(
        "select count(*) from app.pipeline_batches where run_job_id = %s and child_job_id is not null and seq > 0",
        (parent,),
    ).fetchone()
    assert queued == (2,)
