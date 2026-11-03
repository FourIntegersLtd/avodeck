"""Slice A: how far a "Try these pages again" run has got, worked out from the pages themselves.

The run is the case's latest `retry_unread_pages` job. Its pages are the ones the job took when it began
(`page_analysis.retry_job_id`): those whose entries are read since (done), failed again since (failed) and the rest
(waiting). Pages the run did not take are never counted, however recently they were processed; nor are copies,
set-aside and word-less pages. While the run is queued or running it is `running`; a paused one is `paused` (not
running: nothing is moving); a finished run is shown for a few minutes so the page can say how it ended, then
nothing. Counts only (rule 6).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.features.pipeline import repo
from tests.support.db import scope_for
from tests.support.packs import LAUNCH
from tests.support.seed import Seed
from tests.support.uploads import worker_database

pytestmark = pytest.mark.anyio

RETRY = "retry_unread_pages"


def case_of(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    return expert, case, seed.document(case)


def move(admin: psycopg.Connection, job_id: uuid.UUID, *statuses: str) -> None:
    """Walks a job through the statuses the state machine allows, one after the other."""
    for status in statuses:
        admin.execute(
            "update app.jobs set status = %s, finished_at = case when %s = 'done' then now() end where id = %s",
            (status, status, job_id),
        )


def job(
    admin: psycopg.Connection, case: uuid.UUID, *, status: str = "running", finished_ago: str | None = None
) -> uuid.UUID:
    """A retry job queued ten minutes ago, now in `status`."""
    row = admin.execute(
        "insert into app.jobs (case_id, kind, created_at) values (%s, %s, now() - interval '10 minutes') returning id",
        (case, RETRY),
    ).fetchone()
    assert row is not None
    if status != "queued":
        move(admin, row[0], "running", *([] if status == "running" else [status]))
    if finished_ago is not None:
        admin.execute("update app.jobs set finished_at = now() - %s::interval where id = %s", (finished_ago, row[0]))
    return row[0]  # type: ignore[no-any-return]


def page(
    seed: Seed,
    admin: psycopg.Connection,
    case: uuid.UUID,
    doc: uuid.UUID,
    n: int,
    *,
    read: str | None,
    failed: str | None,
    run: uuid.UUID | None = None,
) -> uuid.UUID:
    """A page with words; `read`/`failed` say how long ago its entries were read / failed (None: never); `run` is
    the retry job that took it, if any."""
    p = seed.page(case, doc, n)
    admin.execute("update app.pages set text_source = 'ocr' where id = %s", (p,))
    admin.execute(
        "insert into app.page_analysis"
        " (page_id, bundle_id, stage, extracted_at, entries_extracted_at, entries_failed_at, retry_job_id)"
        " values (%s, (select bundle_id from app.cases where id = %s), 'extract', now(),"
        " now() - %s::interval, now() - %s::interval, %s)",
        (p, case, read, failed, run),
    )
    return p


async def progress(api_db: Database, expert: uuid.UUID, case: uuid.UUID) -> Any:
    async with api_db.request(scope_for(expert)) as conn:
        return await repo.retry_progress(conn, case)


async def test_the_run_counts_done_failed_again_and_still_waiting(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    run = job(admin, case)
    page(seed, admin, case, doc, 1, read="1 minute", failed=None, run=run)  # read well since the run began
    page(seed, admin, case, doc, 2, read="2 minutes", failed=None, run=run)
    page(seed, admin, case, doc, 3, read=None, failed="1 minute", run=run)  # failed again since the run began
    page(seed, admin, case, doc, 4, read=None, failed="1 hour", run=run)  # still waiting its turn
    page(seed, admin, case, doc, 5, read=None, failed="1 hour", run=run)
    page(seed, admin, case, doc, 6, read="1 day", failed=None)  # read long before: not part of the run
    got = await progress(api_db, expert, case)
    assert (got.running, got.paused, got.total, got.done, got.failed) == (True, False, 5, 2, 1)


async def test_pages_the_run_did_not_take_are_not_counted_even_when_processed_inside_the_window(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    earlier = job(admin, case, status="done", finished_ago="9 minutes")
    run = job(admin, case)
    page(seed, admin, case, doc, 1, read=None, failed="1 hour", run=run)
    page(seed, admin, case, doc, 2, read="1 minute", failed=None)  # a new upload read while the run was going
    page(seed, admin, case, doc, 3, read=None, failed="1 minute")  # another page that failed, not taken by it
    page(seed, admin, case, doc, 4, read="1 minute", failed=None, run=earlier)  # taken by an earlier run
    got = await progress(api_db, expert, case)
    assert (got.total, got.done, got.failed) == (1, 0, 0)


async def test_copies_set_aside_and_word_less_pages_are_not_counted(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    run = job(admin, case)
    first = page(seed, admin, case, doc, 1, read=None, failed="1 hour", run=run)
    copy = page(seed, admin, case, doc, 2, read=None, failed="1 hour", run=run)
    held = page(seed, admin, case, doc, 3, read=None, failed="1 hour", run=run)
    blank = page(seed, admin, case, doc, 4, read=None, failed="1 hour", run=run)
    admin.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (first, copy))
    admin.execute("update app.pages set quarantined = true where id = %s", (held,))
    admin.execute("update app.pages set text_source = null where id = %s", (blank,))
    got = await progress(api_db, expert, case)
    assert (got.total, got.done, got.failed) == (1, 0, 0)


async def test_no_retry_ever_run_means_no_progress(api_db: Database, admin: psycopg.Connection, seed: Seed) -> None:
    expert, case, doc = case_of(seed)
    page(seed, admin, case, doc, 1, read=None, failed="1 hour")
    admin.execute("insert into app.jobs (case_id, kind) values (%s, 'process_bundle')", (case,))
    assert await progress(api_db, expert, case) is None


async def test_a_queued_run_is_running_a_paused_one_is_paused_and_a_finished_one_is_neither(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    first = job(admin, case, status="queued")
    page(seed, admin, case, doc, 1, read=None, failed="1 hour", run=first)
    got = await progress(api_db, expert, case)
    assert (got.running, got.paused) == (True, False)
    move(admin, first, "running", "paused")
    got = await progress(api_db, expert, case)
    assert (got.running, got.paused) == (False, True)  # nothing is moving: the page must not wait for it
    move(admin, first, "running", "done")
    got = await progress(api_db, expert, case)
    assert (got.running, got.paused, got.total) == (False, False, 1)


async def test_a_run_finished_long_ago_is_forgotten_and_only_the_latest_run_counts(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    old = job(admin, case, status="done", finished_ago="2 hours")
    page(seed, admin, case, doc, 1, read=None, failed="1 hour", run=old)
    assert await progress(api_db, expert, case) is None
    latest = job(admin, case)
    page(seed, admin, case, doc, 2, read=None, failed="1 hour", run=latest)
    got = await progress(api_db, expert, case)
    assert (got.running, got.total) == (True, 1)


async def test_the_retry_job_marks_the_pages_it_takes(worker_dsn: str, admin: psycopg.Connection, seed: Seed) -> None:
    _, case, doc = case_of(seed)
    run = job(admin, case)
    taken = page(seed, admin, case, doc, 1, read=None, failed="1 hour")
    left = page(seed, admin, case, doc, 2, read="1 hour", failed=None)  # read well: not retryable
    async with worker_database(worker_dsn) as worker, worker.job(case) as conn:  # the worker takes the pages
        assert await repo.reset_unread(conn, case, run) == 1
    marks = dict(admin.execute("select page_id, retry_job_id from app.page_analysis").fetchall())
    assert marks[taken] == run and marks[left] is None
