"""Record entries: where each entry in a record starts and ends on its page (slice A writes, B reads).

An entry is a run of words on one page, found by T-07b (ADR 0028). A row names the page, the first and last
word by their number on the page (`ocr_words.n`), the entry's kind, who wrote it and its specialty (pack
keys), and the date printed inside it. It carries no page text: slice B lays the words out itself, from the
OCR words, behind the blind-read gate.

- `entries_for_case(conn, case_id, page_ids=None)`: the rows, newest-first is not implied: ordered by event
  date (undated last), then page number, then first word.
- `entries_ready(conn, case_id)`: every page the pipeline has read has had its entries read too, so the
  transcript can be drawn. A case read before entries existed is not ready until `request_entries` has run.
- `entries_status(conn, case_id)`: `idle` (not ready, nothing queued), `preparing` (the job is queued or
  running), `ready`, or `failed` (the job failed; ask again).
- `request_entries(conn, case_id)`: queues the job that reads entries for a case that has none. Safe to call
  again: it queues nothing while one is waiting, when the case is ready, or while pages are still waiting to be
  extracted by the pipeline (the entries job could do nothing for them; it would only run again and again).
- `retryable_unread_pages(conn, case_id)` / `request_unread_retry(conn, case_id)`: the pages the model could not
  read (extracted, answer failed, not set aside, not copies), and the job that asks about just those again. It
  queues nothing when none are unread, while one is waiting, or while the pipeline runs. A waiting retry shows as
  `preparing` in `entries_status`.
- `case_over_allowance(conn, case_id, guard=None)`: the case's tokens have reached the cost guard's allowance
  (`cost_guard()`, the worker's own test). `request_unread_retry` refuses to hand a paused run back then (409
  `allowance_used_up`, nothing queued or changed), since the run would only pause again.
- `entries_progress(conn, case_id)`: how many pages there are (`total`), how many are `required` (set-aside
  copies, those with `duplicate_of_page_id`, wait for nothing; a kept copy has it cleared and counts), how many of
  those the pipeline has extracted, and how many have had their entries read (or failed); counts only.
- `pipeline_running(conn, case_id)`: a bundle-processing job is queued or running for the case (a paused one is not).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from psycopg import AsyncConnection

from app.core.config import CostGuard, cost_guard
from app.core.errors import ApiError
from app.core.jobs import registry

EXTRACT_ENTRIES = "extract_entries"
RETRY_UNREAD = "retry_unread_pages"
EntriesStatus = Literal["idle", "preparing", "ready", "failed"]


@dataclass(frozen=True)
class RecordEntry:
    id: uuid.UUID
    page_id: uuid.UUID
    page_no: int
    kind: str
    first_n: int
    last_n: int
    author_role: str | None
    specialty: str | None
    event_date: dt.date | None
    date_basis: str | None


async def entries_for_case(
    conn: AsyncConnection, case_id: uuid.UUID, *, page_ids: Sequence[uuid.UUID] | None = None
) -> list[RecordEntry]:
    cur = await conn.execute(
        "select e.id, e.page_id, p.bundle_page_no, e.kind, e.first_n, e.last_n, e.author_role, e.specialty,"
        " e.event_date, e.date_basis"
        " from app.record_entries e join app.pages p on p.id = e.page_id"
        " where e.bundle_id = (select bundle_id from app.cases where id = %s) and (%s::uuid[] is null or e.page_id = "
        "any(%s::uuid[]))"
        " order by e.event_date nulls last, p.bundle_page_no, e.first_n",
        (case_id, list(page_ids) if page_ids is not None else None, list(page_ids) if page_ids is not None else None),
    )
    return [RecordEntry(*row) for row in await cur.fetchall()]


async def entries_ready(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    """No page the entries job reads is still waiting: every page that is not set aside has been extracted
    and has had its entries read or failed (a person reads those). No such pages at all is ready."""
    cur = await conn.execute(
        "select count(*) from app.pages p left join app.page_analysis a on a.page_id = p.id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and not p.quarantined and "
        "p.duplicate_of_page_id is null and (a.extracted_at is null or"
        " (a.entries_extracted_at is null and a.entries_failed_at is null))",
        (case_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return bool(row[0] == 0)


async def entries_unread_pages(conn: AsyncConnection, case_id: uuid.UUID) -> list[int]:
    """Page numbers whose entries could not be read (the model failed); a person reads them, they are not empty."""
    cur = await conn.execute(
        "select p.bundle_page_no from app.page_analysis a join app.pages p on p.id = a.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and a.extracted_at is not null and not "
        "p.quarantined"
        " and a.entries_extracted_at is null and a.entries_failed_at is not null order by p.bundle_page_no",
        (case_id,),
    )
    return [r[0] for r in await cur.fetchall()]


async def entries_status(conn: AsyncConnection, case_id: uuid.UUID) -> EntriesStatus:
    cur = await conn.execute(
        "select status from app.jobs where case_id = %s and kind = any(%s) order by created_at desc limit 1",
        (case_id, [EXTRACT_ENTRIES, RETRY_UNREAD]),
    )
    row = await cur.fetchone()
    if row is not None and row[0] in ("queued", "running", "paused"):
        return "preparing"
    if await entries_ready(conn, case_id):
        return "ready"
    return "failed" if row is not None and row[0] == "failed" else "idle"


@dataclass(frozen=True)
class EntriesProgress:
    """`total`: every page of the bundle that is not set aside by quarantine (shown as-is). `required`: the pages
    the pipeline and the entries job read, which leaves out set-aside copies (`duplicate_of_page_id`); a copy
    marked kept has that cleared, so it counts. `extracted` and `entries_done` count required pages only."""

    total: int
    extracted: int
    entries_done: int
    required: int


async def entries_progress(conn: AsyncConnection, case_id: uuid.UUID) -> EntriesProgress:
    cur = await conn.execute(
        "select count(*), count(a.extracted_at) filter (where p.duplicate_of_page_id is null),"
        " count(*) filter (where p.duplicate_of_page_id is null and"
        " (a.entries_extracted_at is not null or a.entries_failed_at is not null)),"
        " count(*) filter (where p.duplicate_of_page_id is null)"
        " from app.pages p left join app.page_analysis a on a.page_id = p.id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and not p.quarantined",
        (case_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return EntriesProgress(row[0], row[1], row[2], row[3])


PIPELINE_JOBS = ("process_bundle", "process_pages", "reprocess_case", "scan_document")


async def pipeline_running(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    cur = await conn.execute(
        "select 1 from app.jobs where case_id = %s and kind = any(%s) and status in ('queued', 'running') limit 1",
        (case_id, list(PIPELINE_JOBS)),
    )
    return await cur.fetchone() is not None


async def request_entries(conn: AsyncConnection, case_id: uuid.UUID) -> uuid.UUID | None:
    """Queues the entries job unless the case is ready or one is already waiting; returns its job id."""
    if await entries_status(conn, case_id) in ("ready", "preparing"):
        return None
    progress = await entries_progress(conn, case_id)
    if progress.extracted < progress.required:
        return None  # pages still wait on the pipeline; an entries job now would finish at once and be asked again
    return await registry.enqueue(conn, case_id, EXTRACT_ENTRIES)


async def retryable_unread_page_ids(conn: AsyncConnection, case_id: uuid.UUID) -> list[uuid.UUID]:
    """The one definition of the pages that can be tried again, used by the retry job, the bundle summary and
    the transcript button: the model failed on them (whatever the label), they have words to read, they are
    not set aside and not copies. The failed marker stays until a later extraction succeeds, so a retry that
    itself fails leaves them here."""
    cur = await conn.execute(
        "select p.id from app.page_analysis a join app.pages p on p.id = a.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s)"
        " and a.entries_extracted_at is null and a.entries_failed_at is not null and p.text_source is not null"
        " and not p.quarantined and p.duplicate_of_page_id is null"
        " order by p.bundle_page_no",
        (case_id,),
    )
    return [r[0] for r in await cur.fetchall()]


async def retryable_unread_pages(conn: AsyncConnection, case_id: uuid.UUID) -> int:
    return len(await retryable_unread_page_ids(conn, case_id))


async def request_unread_retry(conn: AsyncConnection, case_id: uuid.UUID) -> uuid.UUID | None:
    """Queues the job that asks again about the unread pages only; nothing queued when there are none, when one
    is waiting, or while the pipeline runs (it would meet the same pages). Presses are taken one at a time per
    case, so two at once queue one job."""
    await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"retry_unread_pages:{case_id}",))
    if await retryable_unread_pages(conn, case_id) == 0:
        return None
    if await pipeline_running(conn, case_id):
        return None
    paused = await _paused_retry(conn, case_id)
    if paused is not None:
        # A pause that is not resumable (the cost guard, the key service) never ends by itself: a press hands the
        # same job back to the queue, never a second job. If the pause comes back, the summary says so again.
        # Not while the case is over its allowance: that run would pause again at once.
        if await case_over_allowance(conn, case_id):
            raise ApiError("allowance_used_up", "This case has used up its processing allowance.", 409)
        await _hand_back(conn, paused)
        return paused
    if await entries_status(conn, case_id) == "preparing":
        return None
    return await registry.enqueue(conn, case_id, RETRY_UNREAD)


async def case_over_allowance(conn: AsyncConnection, case_id: uuid.UUID, guard: CostGuard | None = None) -> bool:
    """The case's tokens used have reached the cost guard's allowance for its pages: the guard's own test."""
    cur = await conn.execute("select tokens_used, pages from app.ai_budget(%s)", (case_id,))
    budget = await cur.fetchone()
    return budget is not None and int(budget[0]) >= (guard or cost_guard()).allowance(int(budget[1]))


async def _paused_retry(conn: AsyncConnection, case_id: uuid.UUID) -> uuid.UUID | None:
    """The case's latest entries job, when it is a retry of the unread pages and is paused."""
    cur = await conn.execute(
        "select id, kind, status from app.jobs where case_id = %s and kind = any(%s) order by created_at desc limit 1",
        (case_id, [EXTRACT_ENTRIES, RETRY_UNREAD]),
    )
    row = await cur.fetchone()
    return row[0] if row is not None and row[1] == RETRY_UNREAD and row[2] == "paused" else None


async def _hand_back(conn: AsyncConnection, job_id: uuid.UUID) -> bool:
    """paused -> queue through `app.resume_paused_job()` (migration 0133); False when it was not paused, is already
    handed back and waiting, or the case is on hold. The database takes the priority and lock from the job."""
    cur = await conn.execute("select app.resume_paused_job(%s, %s)", (job_id, registry.queue))
    row = await cur.fetchone()
    return bool(row and row[0])


async def retry_waiting(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    """A retry of the unread pages is queued or running."""
    cur = await conn.execute(
        "select 1 from app.jobs where case_id = %s and kind = %s and status in ('queued', 'running', 'paused') limit 1",
        (case_id, RETRY_UNREAD),
    )
    return await cur.fetchone() is not None
