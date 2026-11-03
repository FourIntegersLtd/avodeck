"""Slice A: how far a "Try these pages again" run has got, worked out from the pages themselves (no new table).

The run is the case's latest `retry_unread_pages` job. Its pages are those whose entries are read since the job
was queued (done), failed again since (failed) and those still failed from before it (waiting); copies, set-aside
and word-less pages are never counted. While the run is queued or running it is `running`; a finished run is
shown for a few minutes so the page can say how it ended, then nothing. Counts only (rule 6).
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
) -> uuid.UUID:
    """A page with words; `read`/`failed` say how long ago its entries were read / failed (None: never)."""
    p = seed.page(case, doc, n)
    admin.execute("update app.pages set text_source = 'ocr' where id = %s", (p,))
    admin.execute(
        "insert into app.page_analysis"
        " (page_id, bundle_id, stage, extracted_at, entries_extracted_at, entries_failed_at)"
        " values (%s, (select bundle_id from app.cases where id = %s), 'extract', now(),"
        " now() - %s::interval, now() - %s::interval)",
        (p, case, read, failed),
    )
    return p


async def progress(api_db: Database, expert: uuid.UUID, case: uuid.UUID) -> Any:
    async with api_db.request(scope_for(expert)) as conn:
        return await repo.retry_progress(conn, case)


async def test_the_run_counts_done_failed_again_and_still_waiting(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    job(admin, case)
    page(seed, admin, case, doc, 1, read="1 minute", failed=None)  # read well since the run began
    page(seed, admin, case, doc, 2, read="2 minutes", failed=None)
    page(seed, admin, case, doc, 3, read=None, failed="1 minute")  # failed again since the run began
    page(seed, admin, case, doc, 4, read=None, failed="1 hour")  # still waiting its turn
    page(seed, admin, case, doc, 5, read=None, failed="1 hour")
    page(seed, admin, case, doc, 6, read="1 day", failed=None)  # read long before: not part of the run
    got = await progress(api_db, expert, case)
    assert (got.running, got.total, got.done, got.failed) == (True, 5, 2, 1)


async def test_copies_set_aside_and_word_less_pages_are_not_counted(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    job(admin, case)
    first = page(seed, admin, case, doc, 1, read=None, failed="1 hour")
    copy = page(seed, admin, case, doc, 2, read=None, failed="1 hour")
    held = page(seed, admin, case, doc, 3, read=None, failed="1 hour")
    blank = page(seed, admin, case, doc, 4, read=None, failed="1 hour")
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


async def test_a_queued_or_paused_run_is_running_and_a_finished_one_is_not(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    page(seed, admin, case, doc, 1, read=None, failed="1 hour")
    first = job(admin, case, status="queued")
    assert (await progress(api_db, expert, case)).running is True
    move(admin, first, "running", "paused")
    assert (await progress(api_db, expert, case)).running is True
    move(admin, first, "running", "done")
    got = await progress(api_db, expert, case)
    assert (got.running, got.total) == (False, 1)


async def test_a_run_finished_long_ago_is_forgotten_and_only_the_latest_run_counts(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    page(seed, admin, case, doc, 1, read=None, failed="1 hour")
    job(admin, case, status="done", finished_ago="2 hours")
    assert await progress(api_db, expert, case) is None
    job(admin, case)
    assert (await progress(api_db, expert, case)).running is True
