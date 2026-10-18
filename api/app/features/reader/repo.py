"""SQL for slice B's tables (`page_reads`, `fact_verifications`, `fact_flags`) and its writes on `pages` and `facts`.

Every function runs on the request's or job's connection, so row-level security applies (ADR 0013).
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core.contracts.document_words import box, word_purpose
from app.core.contracts.fact_flags import POINTS, RECHECK
from app.core.contracts.pipeline import transcription_purpose
from app.core.crypto import CaseCrypto


async def record_read(
    conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, person_id: uuid.UUID, dwell_seconds: int
) -> None:
    """Append one read to the case's read log (the database numbers, times and hashes it)."""
    await conn.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, %s)",
        (case_id, page_id, person_id, dwell_seconds),
    )


async def has_read(conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> bool:
    cur = await conn.execute(
        "select exists (select 1 from app.page_reads r join app.pages p on p.id = r.page_id"
        " where r.case_id = %s and p.bundle_page_no = %s and r.person_id = %s)",
        (case_id, page_no, person_id),
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def citing_read_page_ids(conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> set[uuid.UUID]:
    cur = await conn.execute(
        "select distinct page_id from app.page_reads where case_id = %s and person_id = %s and counts_for_citing",
        (case_id, person_id),
    )
    return {r[0] for r in await cur.fetchall()}


# Pages (S09) ---------------------------------------------------------------------------------------

DEFAULT_SECONDS_PER_PAGE = 20

_PAGE_COLUMNS = """
    p.bundle_page_no, p.id, p.source_type,
    exists (select 1 from app.page_reads r where r.page_id = p.id and r.person_id = %(person)s) as read,
    case when c.period_start is null or c.period_end is null then null
         else coalesce((select bool_or(d between c.period_start and c.period_end)
                        from app.page_analysis a, unnest(a.printed_dates) d where a.page_id = p.id), false)
    end as in_period,
    p.unreadable,
    (select o.bundle_page_no from app.pages o where o.id = p.duplicate_of_page_id) as duplicate_of_page_no
"""

_DETAIL_COLUMNS = """,
    p.rotation, p.relevance, p.needs_clearer_copy,
    (select s.bundle_page_no from app.pages s where s.id = p.superseded_by_page_id) as superseded_by_page_no,
    p.handwriting, p.transcribed_by_expert_text is not null as has_transcription,
    (select min(r.at) from app.page_reads r where r.page_id = p.id and r.person_id = %(person)s) as first_read_at
"""

_FILTERS = {
    None: "",
    "unread": "and not exists (select 1 from app.page_reads r where r.page_id = p.id and r.person_id = %(person)s)",
    "period": """and c.period_start is not null and c.period_end is not null and exists (
                   select 1 from app.page_analysis a, unnest(a.printed_dates) d
                   where a.page_id = p.id and d between c.period_start and c.period_end)""",
    "source": "and p.source_type is not distinct from %(source)s",
}


async def list_pages(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    person_id: uuid.UUID,
    *,
    filter: str | None,
    source: str | None,
    cursor: int,
    limit: int,
) -> list[dict[str, Any]]:
    """The case's pages after `cursor` in page order (at most `limit`), as the person sees them."""
    cur = await conn.execute(
        f"""select {_PAGE_COLUMNS} from app.pages p join app.cases c on c.id = p.case_id
            where p.case_id = %(case)s and not p.quarantined and p.bundle_page_no > %(cursor)s {_FILTERS[filter]}
            order by p.bundle_page_no limit %(limit)s""",  # noqa: S608 (fixed fragments only)
        {"case": case_id, "person": person_id, "source": source, "cursor": cursor, "limit": limit},
    )
    return [_row(cur, r) for r in await cur.fetchall()]


async def page(conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> dict[str, Any] | None:
    """One page the person may see (not set aside), with its details; None when there is none."""
    cur = await conn.execute(
        f"""select {_PAGE_COLUMNS} {_DETAIL_COLUMNS}, p.image_path, p.duplicate_of_page_id
            from app.pages p join app.cases c on c.id = p.case_id
            where p.case_id = %(case)s and p.bundle_page_no = %(page_no)s and not p.quarantined""",  # noqa: S608
        {"case": case_id, "page_no": page_no, "person": person_id},
    )
    row = await cur.fetchone()
    return _row(cur, row) if row else None


async def page_facts(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> list[dict[str, Any]]:
    """The page's facts in reading order, including ones marked not right (so that can be undone)."""
    cur = await conn.execute(
        f"""select {_FACT_COLUMNS}
           from app.facts f where f.case_id = %(case)s and f.page_id = %(page)s
           order by (f.bbox ->> 'y0')::float nulls last, f.created_at, f.id""",  # noqa: S608 (fixed fragment)
        {"case": case_id, "page": page_id, "points": POINTS, "recheck": RECHECK},
    )
    return [_row(cur, r) for r in await cur.fetchall()]


async def progress(conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> tuple[int, int, int]:
    """(pages read, pages, seconds left). Each unread page takes the person's median dwell for its kind of
    source, else their median over every page, else `DEFAULT_SECONDS_PER_PAGE`."""
    cur = await conn.execute(
        """with shown as (select p.id, p.source_type from app.pages p where p.case_id = %(case)s and not p.quarantined),
                mine as (select s.source_type, r.page_id, r.dwell_seconds from app.page_reads r
                         join shown s on s.id = r.page_id where r.person_id = %(person)s),
                pace as (select source_type, percentile_cont(0.5) within group (order by dwell_seconds) as m
                         from mine group by source_type),
                overall as (select percentile_cont(0.5) within group (order by dwell_seconds) as m from mine)
           select (select count(distinct page_id) from mine), (select count(*) from shown),
                  coalesce((select sum(coalesce(pace.m, overall.m, %(default)s))
                            from shown s cross join overall
                            left join pace on pace.source_type is not distinct from s.source_type
                            where s.id not in (select page_id from mine)), 0)""",
        {"case": case_id, "person": person_id, "default": DEFAULT_SECONDS_PER_PAGE},
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0]), int(row[1]), round(row[2])


def _row(cur: Any, row: tuple[Any, ...]) -> dict[str, Any]:
    return {d.name: v for d, v in zip(cur.description, row, strict=True)}


# Checking facts (S09) ------------------------------------------------------------------------------

# `flagged` is the Points against flag only; `needs_recheck` is "Please check" after a page is read again.
_FACT_COLUMNS = """f.id, f.kind, f.fields, f.value, f.word_ids, f.bbox, f.event_date, f.status, f.version,
    exists (select 1 from app.fact_flags g where g.fact_id = f.id and g.flagged_for = %(points)s) as flagged,
    exists (select 1 from app.fact_flags g where g.fact_id = f.id and g.flagged_for = %(recheck)s) as needs_recheck"""
_FLAGS = {"points": POINTS, "recheck": RECHECK}


async def fact(conn: AsyncConnection, case_id: uuid.UUID, fact_id: uuid.UUID) -> dict[str, Any] | None:
    """A fact on a page that is not set aside, with its page's id, number and clearer copy."""
    cur = await conn.execute(
        f"""select {_FACT_COLUMNS}, f.page_id, p.bundle_page_no as page_no, p.superseded_by_page_id
            from app.facts f join app.pages p on p.id = f.page_id
            where f.case_id = %(case)s and f.id = %(fact)s and not p.quarantined""",  # noqa: S608
        {"case": case_id, "fact": fact_id, **_FLAGS},
    )
    row = await cur.fetchone()
    return _row(cur, row) if row else None


async def page_words(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, page_id: uuid.UUID
) -> dict[str, str]:
    """The page's words in reading order, unsealed (kept in memory only, never logged)."""
    cur = await conn.execute("select word_id, text from app.ocr_words where page_id = %s order by n", (page_id,))
    return {
        w: (await crypto.decrypt(conn, case_id, bytes(t), purpose=word_purpose(page_id, w))).decode()
        for w, t in await cur.fetchall()
    }


async def change_fact(
    conn: AsyncConnection,
    fact_id: uuid.UUID,
    version: int,
    *,
    status: str,
    value: str | None,
    word_ids: list[str],
    page_id: uuid.UUID,
    bbox: Any,
    fields: dict[str, Any],
    event_date: Any,
) -> dict[str, Any] | None:
    """Apply a check if the fact is still at `version` (the database bumps it); None when it moved on."""
    cur = await conn.execute(
        f"""update app.facts f set status = %(status)s, value = %(value)s, word_ids = %(word_ids)s,
                page_id = %(page)s, bbox = %(bbox)s, fields = %(fields)s, event_date = %(event_date)s
            where f.id = %(fact)s and f.version = %(version)s returning {_FACT_COLUMNS}""",  # noqa: S608
        {
            "status": status,
            "value": value,
            "word_ids": word_ids,
            "page": page_id,
            "bbox": Jsonb(bbox) if bbox is not None else None,
            "fields": Jsonb(fields),
            "event_date": event_date,
            "fact": fact_id,
            "version": version,
            **_FLAGS,
        },
    )
    row = await cur.fetchone()
    return _row(cur, row) if row else None


async def word_box(conn: AsyncConnection, page_id: uuid.UUID, word_ids: list[str]) -> dict[str, float] | None:
    """The cited words' box as a share of the page (as slice A boxes facts); None when the words have no
    place on the picture (typed out by the expert)."""
    cur = await conn.execute("select word_id, polygon from app.ocr_words where page_id = %s", (page_id,))
    polygons = {w: [float(v) for v in p] for w, p in await cur.fetchall()}
    cited = [w for w in word_ids if w in polygons]
    if not cited or not any(v for w in cited for v in polygons[w]):
        return None
    cur = await conn.execute("select width, height from app.page_analysis where page_id = %s", (page_id,))
    size = await cur.fetchone()
    width = float(size[0]) if size and size[0] else max((x for p in polygons.values() for x in p[0::2]), default=1.0)
    height = float(size[1]) if size and size[1] else max((y for p in polygons.values() for y in p[1::2]), default=1.0)
    return box(cited, polygons, width or 1.0, height or 1.0)


async def log_check(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    person_id: uuid.UUID,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    reason: str | None = None,
    new_page_id: uuid.UUID | None = None,
) -> None:
    """Append the check to the case's verification log (the database numbers and hashes it)."""
    await conn.execute(
        """insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value,
               old_fields, new_fields, old_word_ids, new_word_ids, old_page_id, new_page_id, reason)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            case_id,
            before["id"],
            person_id,
            action,
            before["value"],
            after["value"],
            Jsonb(before["fields"]),
            Jsonb(after["fields"]),
            before["word_ids"],
            after["word_ids"],
            before["page_id"] if new_page_id else None,
            new_page_id,
            reason,
        ),
    )


async def set_flag(
    conn: AsyncConnection, case_id: uuid.UUID, fact_id: uuid.UUID, flagged_for: str, person_id: uuid.UUID, on: bool
) -> None:
    if on:
        await conn.execute(
            "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, %s, %s)"
            " on conflict (fact_id, flagged_for) do nothing",
            (case_id, fact_id, flagged_for, person_id),
        )
    else:
        await conn.execute("delete from app.fact_flags where fact_id = %s and flagged_for = %s", (fact_id, flagged_for))


async def page_id_by_no(conn: AsyncConnection, case_id: uuid.UUID, page_no: int) -> uuid.UUID | None:
    cur = await conn.execute(
        "select id from app.pages where case_id = %s and bundle_page_no = %s and not quarantined", (case_id, page_no)
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def unreadable_pages(conn: AsyncConnection, case_id: uuid.UUID) -> list[dict[str, Any]]:
    """The pages the computer couldn't read (not set aside), in page order, with what the expert has done."""
    cur = await conn.execute(
        """select p.bundle_page_no as page_no, p.id as page_id, p.source_type, p.handwriting, p.relevance,
                  p.transcribed_by_expert_text is not null as has_transcription, p.needs_clearer_copy
           from app.pages p where p.case_id = %s and p.unreadable and not p.quarantined
           order by p.bundle_page_no""",
        (case_id,),
    )
    return [_row(cur, r) for r in await cur.fetchall()]


async def save_transcription(
    conn: AsyncConnection,
    crypto: CaseCrypto,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    text: str,
    relevance: str | None,
) -> None:
    """Stores the expert's typing sealed with the case key (never plain, like every OCR word)."""
    sealed = await crypto.encrypt(conn, case_id, text.encode(), purpose=transcription_purpose(page_id))
    await conn.execute(
        "update app.pages set transcribed_by_expert_text = %s, relevance = coalesce(%s, relevance) where id = %s",
        (sealed, relevance, page_id),
    )


async def change_page(conn: AsyncConnection, page_id: uuid.UUID, changes: dict[str, Any]) -> None:
    """Sets the given `relevance` and `needs_clearer_copy` (only those keys)."""
    sets = ", ".join(f"{k} = %({k})s" for k in changes if k in ("relevance", "needs_clearer_copy"))
    await conn.execute(f"update app.pages set {sets} where id = %(id)s", {**changes, "id": page_id})  # noqa: S608


async def rotate(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, degrees: int) -> int | None:
    """Turns the page clockwise; its new rotation, or None when the case has no such page."""
    cur = await conn.execute(
        "update app.pages set rotation = (rotation + %s) %% 360"
        " where id = %s and case_id = %s and not quarantined returning rotation",
        (degrees, page_id, case_id),
    )
    row = await cur.fetchone()
    return None if row is None else int(row[0])


async def pages_to_type(conn: AsyncConnection, case_ids: list[uuid.UUID]) -> list[tuple[uuid.UUID, int]]:
    """(case, unreadable pages not yet typed out or sent for a clearer copy) where there are any."""
    cur = await conn.execute(
        """select p.case_id, count(*)::int from app.pages p
           where p.case_id = any(%s) and p.unreadable and not p.quarantined
             and p.transcribed_by_expert_text is null and not p.needs_clearer_copy
           group by p.case_id""",
        (case_ids,),
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]
