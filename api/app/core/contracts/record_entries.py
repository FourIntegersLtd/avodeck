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
  again: it queues nothing while one is waiting or when the case is ready.
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
        "select status from app.jobs where case_id = %s and kind = %s order by created_at desc limit 1",
        (case_id, EXTRACT_ENTRIES),
    )
    row = await cur.fetchone()
    if row is not None and row[0] in ("queued", "running", "paused"):
        return "preparing"
    if await entries_ready(conn, case_id):
        return "ready"
    return "failed" if row is not None and row[0] == "failed" else "idle"


async def request_entries(conn: AsyncConnection, case_id: uuid.UUID) -> uuid.UUID | None:
    """Queues the entries job unless the case is ready or one is already waiting; returns its job id."""
    if await entries_status(conn, case_id) in ("ready", "preparing"):
        return None
    return await registry.enqueue(conn, case_id, EXTRACT_ENTRIES)
