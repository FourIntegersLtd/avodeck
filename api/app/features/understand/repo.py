"""SQL for slice D: the facts a person may see, and the latest availability override of each.

Checked facts (verified, edited) show everywhere; suggestions only on pages the person has read
(rule 3, through slice B's read log; with no log nothing unchecked shows); rejected facts never.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import Request
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.core.contracts.reading import PageReadLog

CHECKED = ("verified", "edited")


@dataclass(frozen=True)
class SeenFact:
    fact_id: uuid.UUID
    page_id: uuid.UUID
    page_no: int
    kind: str
    fields: dict[str, Any]
    value: str | None
    event_date: date | None
    available_at: date | None
    status: str
    version: int
    source: str | None = None
    bbox: Any = None
    date_basis: str | None = None
    first_word_n: int | None = None


async def person_id(conn: AsyncConnection) -> uuid.UUID:
    row = await (await conn.execute("select app.current_person_id()")).fetchone()
    assert row is not None and row[0] is not None
    return uuid.UUID(str(row[0]))


async def read_pages(request: Request, conn: AsyncConnection, case_id: uuid.UUID) -> set[uuid.UUID]:
    log: PageReadLog | None = getattr(request.app.state, "page_reads", None)
    if log is None:
        return set()
    return await log.read_page_ids(conn, case_id, await person_id(conn))


_FACTS = (
    "select f.id as fact_id, f.page_id, p.bundle_page_no as page_no, f.kind, f.fields, f.value, f.event_date,"
    " coalesce(o.at, (f.available_at at time zone 'UTC')::date) as available_at, o.unknown, f.status, f.version,"
    " p.source_type as source, f.bbox, f.date_basis, w.n as first_word_n"
    " from app.facts f join app.pages p on p.id = f.page_id"
    " left join app.ocr_words w on w.page_id = f.page_id and w.word_id = f.word_ids[1]"
    " left join lateral (select ao.available_at::date as at, ao.unknown from app.availability_overrides ao"
    "   where ao.fact_id = f.id order by ao.created_at desc, ao.id desc limit 1) o on true"
    " where f.case_id = %s and f.status <> 'rejected'"
)


async def visible_facts(request: Request, conn: AsyncConnection, case_id: uuid.UUID) -> list[SeenFact]:
    read = await read_pages(request, conn, case_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{_FACTS} order by f.event_date nulls last, p.bundle_page_no, f.id", (case_id,))
        rows = await cur.fetchall()
    out = []
    for row in rows:
        if row["status"] not in CHECKED and row["page_id"] not in read:
            continue
        unknown = row.pop("unknown")
        if unknown:
            row["available_at"] = None
        out.append(SeenFact(**row))
    return out


async def linked_questions(conn: AsyncConnection, case_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    cur = await conn.execute(
        "select fact_id, question_id from app.fact_question_links where case_id = %s and status = 'confirmed'"
        " order by created_at, id",
        (case_id,),
    )
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for fact_id, question_id in await cur.fetchall():
        out.setdefault(fact_id, []).append(question_id)
    return out


SUMMARY_LENGTH = 80


def summary_of(fact: SeenFact) -> str | None:
    """A short label for an entry: the words copied from the page, cut to one line."""
    text = " ".join((fact.value or "").split())
    if not text:
        return None
    return text if len(text) <= SUMMARY_LENGTH else text[: SUMMARY_LENGTH - 1].rstrip() + "…"


async def flagged_facts(conn: AsyncConnection, case_id: uuid.UUID) -> set[uuid.UUID]:
    """Facts an open finding rests on."""
    cur = await conn.execute(
        "select distinct unnest(evidence_fact_ids) from app.findings where case_id = %s and status = 'open'",
        (case_id,),
    )
    return {r[0] for r in await cur.fetchall()}
