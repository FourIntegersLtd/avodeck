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


_BASE = (
    "select f.id as fact_id, f.page_id, p.bundle_page_no as page_no, f.kind, f.fields, f.value, f.event_date,"
    " case when o.unknown then null else coalesce(o.at, (f.available_at at time zone 'UTC')::date) end"
    " as available_at, f.status, f.version,"
    " p.source_type as source, f.bbox, f.date_basis, w.n as first_word_n"
    " from app.facts f join app.pages p on p.id = f.page_id"
    " left join app.ocr_words w on w.page_id = f.page_id and w.word_id = f.word_ids[1]"
    " left join lateral (select ao.available_at::date as at, ao.unknown from app.availability_overrides ao"
    "   where ao.fact_id = f.id order by ao.created_at desc, ao.id desc limit 1) o on true"
    " where f.case_id = %s and f.status <> 'rejected'"
    " and (f.status in ('verified', 'edited') or f.page_id = any(%s::uuid[]))"
)
_LINKED = "exists (select 1 from app.fact_question_links l where l.fact_id = s.fact_id and l.status = 'confirmed'"
_FLAGGED = (
    "exists (select 1 from app.findings x where x.case_id = %s and x.status = 'open'"
    " and s.fact_id = any(x.evidence_fact_ids))"
)
_FAR = date.max
"""Stands in for "no day" when ordering: entries with no day come last."""


@dataclass(frozen=True)
class Filters:
    """What the timeline narrows to; every part is optional and all of them are applied in SQL."""

    since: date | None = None
    until: date | None = None
    status: str | None = None
    question_id: uuid.UUID | None = None
    only: str | None = None
    as_seen_on: date | None = None


@dataclass(frozen=True)
class After:
    """Where the last page ended, in the date order the timeline lists entries (day, page, own date, id)."""

    day: date
    page_no: int
    event_date: date
    fact_id: uuid.UUID


def chrono_key(fact: SeenFact) -> tuple[date, int, date, uuid.UUID]:
    """Dated entries by day (the date the record shows, else when it arrived), then page; no day last."""
    return (fact.event_date or fact.available_at or _FAR, fact.page_no, fact.event_date or _FAR, fact.fact_id)


def _where(case_id: uuid.UUID, f: Filters, after: After | None) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    args: list[Any] = []
    if f.since is not None:
        where.append("s.event_date >= %s")
        args.append(f.since)
    if f.until is not None:
        where.append("s.event_date <= %s")
        args.append(f.until)
    if f.status == "verified":
        where.append("s.status in ('verified', 'edited')")
    elif f.status is not None:
        where.append("s.status = %s")
        args.append(f.status)
    if f.as_seen_on is not None:
        where.append("s.available_at <= %s")
        args.append(f.as_seen_on)
    if f.only == "not_linked":
        where.append(f"not {_LINKED})")
    elif f.only == "things_to_look":
        where.append(_FLAGGED)
        args.append(case_id)
    if f.question_id is not None:
        where.append(f"{_LINKED} and l.question_id = %s)")
        args.append(f.question_id)
    if after is not None:
        where.append(
            "(coalesce(s.event_date, s.available_at, %s::date), s.page_no, coalesce(s.event_date, %s::date), s.fact_id)"
            " > (%s::date, %s, %s::date, %s)"
        )
        args += [_FAR, _FAR, after.day, after.page_no, after.event_date, after.fact_id]
    return where, args


async def visible_facts(
    request: Request,
    conn: AsyncConnection,
    case_id: uuid.UUID,
    filters: Filters | None = None,
    *,
    limit: int | None = None,
    after: After | None = None,
    dated_only: bool = False,
) -> list[SeenFact]:
    """The facts the person may see that pass `filters`. With `limit` or `after`, in timeline order a page at a time."""
    read = await read_pages(request, conn, case_id)
    where, args = _where(case_id, filters or Filters(), after)
    if dated_only:
        where.append("s.event_date is not null")
    paged = limit is not None or after is not None
    order = (
        "coalesce(s.event_date, s.available_at, %s::date), s.page_no, coalesce(s.event_date, %s::date), s.fact_id"
        if paged
        else "s.event_date nulls last, s.page_no, s.fact_id"
    )
    sql = f"select * from ({_BASE}) s{' where ' + ' and '.join(where) if where else ''} order by {order}"
    params: list[Any] = [case_id, list(read)]
    # the placeholders run in text order: base, then the filters, then the order by
    params += args + ([_FAR, _FAR] if paged else [])
    if limit is not None:
        sql += " limit %s"
        params.append(limit)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return [SeenFact(**row) for row in await cur.fetchall()]


@dataclass(frozen=True)
class Totals:
    """Everything the person may see, before any filter: the numbers on the chips and the span of dates."""

    checked: int
    please_check: int
    things_to_look: int
    not_linked: int
    undated: int
    total: int
    first: date | None
    last: date | None


async def totals(request: Request, conn: AsyncConnection, case_id: uuid.UUID) -> Totals:
    read = await read_pages(request, conn, case_id)
    sql = (
        "select count(*) filter (where s.status in ('verified', 'edited')),"
        " count(*) filter (where s.status not in ('verified', 'edited')),"
        f" count(*) filter (where {_FLAGGED}),"
        f" count(*) filter (where not {_LINKED})),"
        " count(*) filter (where s.event_date is null), count(*), min(s.event_date), max(s.event_date)"
        f" from ({_BASE}) s"
    )
    cur = await conn.execute(sql, [case_id, case_id, list(read)])
    row = await cur.fetchone()
    assert row is not None
    return Totals(*row)


async def linked_questions(
    conn: AsyncConnection, case_id: uuid.UUID, fact_ids: list[uuid.UUID] | None = None
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Confirmed question links by fact; with `fact_ids`, only for those facts."""
    cur = await conn.execute(
        "select fact_id, question_id from app.fact_question_links where case_id = %s and status = 'confirmed'"
        " and (%s::uuid[] is null or fact_id = any(%s::uuid[])) order by created_at, id",
        (case_id, fact_ids, fact_ids),
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
