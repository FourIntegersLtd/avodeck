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
- `entries_progress(conn, case_id)`: how many pages there are, how many the pipeline has extracted, and how many
  have had their entries read (or failed); counts only.
- `pipeline_running(conn, case_id)`: a bundle-processing job is queued or running for the case (a paused one is not).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from psycopg import AsyncConnection

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
        "(a.extracted_at is null or"
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
    total: int
    extracted: int
    entries_done: int


async def entries_progress(conn: AsyncConnection, case_id: uuid.UUID) -> EntriesProgress:
    cur = await conn.execute(
        "select count(*), count(a.extracted_at),"
        " count(*) filter (where a.entries_extracted_at is not null or a.entries_failed_at is not null)"
        " from app.pages p left join app.page_analysis a on a.page_id = p.id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and not p.quarantined",
        (case_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return EntriesProgress(row[0], row[1], row[2])


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
    if progress.extracted < progress.total:
        return None  # pages still wait on the pipeline; an entries job now would finish at once and be asked again
    return await registry.enqueue(conn, case_id, EXTRACT_ENTRIES)


async def retryable_unread_page_ids(conn: AsyncConnection, case_id: uuid.UUID) -> list[uuid.UUID]:
    """Pages whose answer failed after the model was asked about them alone: extracted, entries failed,
    not set aside, not copies."""
    cur = await conn.execute(
        "select p.id from app.page_analysis a join app.pages p on p.id = a.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and a.extracted_at is not null"
        " and a.entries_extracted_at is null and a.entries_failed_at is not null and a.reason = 'invalid_output'"
        " and not p.quarantined and p.duplicate_of_page_id is null"
        " order by p.bundle_page_no",
        (case_id,),
    )
    return [r[0] for r in await cur.fetchall()]


async def retryable_unread_pages(conn: AsyncConnection, case_id: uuid.UUID) -> int:
    return len(await retryable_unread_page_ids(conn, case_id))


async def request_unread_retry(conn: AsyncConnection, case_id: uuid.UUID) -> uuid.UUID | None:
    """Queues the job that asks again about the unread pages only; nothing queued when there are none, when one
    is waiting, or while the pipeline runs (it would meet the same pages)."""
    if await retryable_unread_pages(conn, case_id) == 0:
        return None
    if await entries_status(conn, case_id) == "preparing" or await pipeline_running(conn, case_id):
        return None
    return await registry.enqueue(conn, case_id, RETRY_UNREAD)


async def retry_waiting(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    """A retry of the unread pages is queued or running."""
    cur = await conn.execute(
        "select 1 from app.jobs where case_id = %s and kind = %s and status in ('queued', 'running', 'paused') limit 1",
        (case_id, RETRY_UNREAD),
    )
    return await cur.fetchone() is not None
