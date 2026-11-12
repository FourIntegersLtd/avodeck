"""SQL for slice B's tables (`page_reads`, `fact_verifications`, `fact_flags`) and its writes on `pages` and `facts`.

Every function runs on the request's or job's connection, so row-level security applies (ADR 0013).
"""

from __future__ import annotations

import uuid
from array import array
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core.ai import provenance
from app.core.contracts.document_words import (
    Correction,
    apply_corrections,
    box,
    correction_purpose,
    current_corrections,
    open_words,
    read_as_corrected,
    word_purpose,
)
from app.core.contracts.fact_flags import POINTS, RECHECK
from app.core.contracts.pipeline import transcription_purpose
from app.core.crypto import CaseCrypto
from app.features.reader.search_cache import CaseWords, PageWords
from app.features.reader.set_aside import CONFIRMED_COPY


async def record_read(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    person_id: uuid.UUID,
    dwell_seconds: int,
    via: str = "page",
) -> None:
    """Append one read to the case's read log (the database numbers, times and hashes it)."""
    await conn.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds, via) values (%s, %s, %s, %s, %s)",
        (case_id, page_id, person_id, dwell_seconds, via),
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
    (select o.bundle_page_no from app.pages o where o.id = p.duplicate_of_page_id) as duplicate_of_page_no,
    coalesce((select t.degrees from app.page_turns t where t.page_id = p.id), 0) as turn
"""

_DETAIL_COLUMNS = """,
    p.rotation, p.relevance, p.needs_clearer_copy, c.period_start, c.period_end,
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
        f"""select {_PAGE_COLUMNS} from app.pages p join app.cases c on c.id = %(case)s
            where p.bundle_id = c.bundle_id and not p.quarantined and p.bundle_page_no > %(cursor)s {_FILTERS[filter]}
            order by p.bundle_page_no limit %(limit)s""",  # noqa: S608 (fixed fragments only)
        {"case": case_id, "person": person_id, "source": source, "cursor": cursor, "limit": limit},
    )
    return [_row(cur, r) for r in await cur.fetchall()]


async def page(conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> dict[str, Any] | None:
    """One page the person may see (not set aside), with its details; None when there is none."""
    cur = await conn.execute(
        f"""select {_PAGE_COLUMNS} {_DETAIL_COLUMNS}, p.image_path, p.duplicate_of_page_id,
                   {CONFIRMED_COPY} as is_confirmed_copy
            from app.pages p join app.cases c on c.id = %(case)s
            where p.bundle_id = c.bundle_id and p.bundle_page_no = %(page_no)s and not p.quarantined""",  # noqa: S608
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
    facts = [_row(cur, r) for r in await cur.fetchall()]
    for f in facts:
        f["related"] = [
            {
                "fact_id": o["id"],
                "kind": o["kind"],
                "value": o["value"],
                "event_date": o["event_date"],
                "status": o["status"],
            }
            for o in facts
            if o is not f
            and f["event_date"] is not None
            and o["event_date"] == f["event_date"]
            and o["kind"] != f["kind"]
            and o["status"] != "rejected"
        ][:3]
    return facts


async def page_reassigned(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> bool:
    """True when a fact on the page is `suggested` and its latest log row is the system's `reset`: the named
    expert changed and the check went back to be done again (migration 0031)."""
    cur = await conn.execute(
        """select exists (
             select 1 from app.facts f
             where f.case_id = %(case)s and f.page_id = %(page)s and f.status = 'suggested'
               and (select v.action from app.fact_verifications v where v.fact_id = f.id
                    order by v.seq desc limit 1) = 'reset')""",
        {"case": case_id, "page": page_id},
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def page_needs_check(conn: AsyncConnection, page_id: uuid.UUID) -> bool:
    """True when the pipeline flagged this page as one a person must check by hand (false if no row)."""
    cur = await conn.execute("select needs_human from app.page_analysis where page_id = %s", (page_id,))
    row = await cur.fetchone()
    return bool(row and row[0])


async def progress(conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> tuple[int, int, int]:
    """(pages read, pages, seconds left). Each unread page takes the person's median dwell for its kind of
    source, else their median over every page, else `DEFAULT_SECONDS_PER_PAGE`."""
    cur = await conn.execute(
        """with shown as (select p.id, p.source_type from app.pages p
                    where p.bundle_id = (select bundle_id from app.cases where id = %(case)s) and not p.quarantined),
                mine as (select s.source_type, r.page_id, r.dwell_seconds from app.page_reads r
                         join shown s on s.id = r.page_id where r.person_id = %(person)s),
                pace as (select source_type, percentile_cont(0.5) within group (order by dwell_seconds) as m
                         from mine group by source_type),
                overall as (select percentile_cont(0.5) within group (order by dwell_seconds) as m from mine)
           select (select count(distinct page_id) from mine), (select count(*) from shown),
                  coalesce((select sum(coalesce(pace.m, overall.m, %(default)s))
                            from shown s cross join overall
                            left join pace on pace.source_type is not distinct from s.source_type
                            where not exists (select 1 from mine m where m.page_id = s.id)), 0)""",
        {"case": case_id, "person": person_id, "default": DEFAULT_SECONDS_PER_PAGE},
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0]), int(row[1]), round(row[2])


async def progress_many(
    conn: AsyncConnection, case_ids: list[uuid.UUID], person_id: uuid.UUID
) -> dict[uuid.UUID, tuple[int, int, int]]:
    """`progress` for many cases in one query: case id -> (pages read, pages, seconds left)."""
    cur = await conn.execute(
        """with shown as (select c.id as case_id, p.id, p.source_type from app.cases c
                    join app.pages p on p.bundle_id = c.bundle_id and not p.quarantined
                    where c.id = any(%(cases)s)),
                mine as (select s.case_id, s.source_type, r.page_id, r.dwell_seconds from app.page_reads r
                         join shown s on s.id = r.page_id where r.person_id = %(person)s),
                pace as (select case_id, source_type, percentile_cont(0.5) within group (order by dwell_seconds) as m
                         from mine group by case_id, source_type),
                overall as (select case_id, percentile_cont(0.5) within group (order by dwell_seconds) as m
                            from mine group by case_id)
           select c.id,
                  (select count(distinct page_id) from mine where mine.case_id = c.id),
                  (select count(*) from shown where shown.case_id = c.id),
                  coalesce((select sum(coalesce(pace.m, (select o.m from overall o where o.case_id = c.id),
                                                %(default)s))
                            from shown s
                            left join pace on pace.case_id = s.case_id
                                          and pace.source_type is not distinct from s.source_type
                            where s.case_id = c.id
                              and not exists (select 1 from mine m where m.page_id = s.id)), 0)
           from app.cases c where c.id = any(%(cases)s)""",
        {"cases": case_ids, "person": person_id, "default": DEFAULT_SECONDS_PER_PAGE},
    )
    return {r[0]: (int(r[1]), int(r[2]), round(r[3])) for r in await cur.fetchall()}


def _row(cur: Any, row: tuple[Any, ...]) -> dict[str, Any]:
    return {d.name: v for d, v in zip(cur.description, row, strict=True)}


# How a date the expert typed is labelled (not a date read from the page). Foundation: add the same constant
# beside DATE_BASIS_PRINTED in core/contracts/fact_dates.py and import it from there.
DATE_BASIS_EXPERT = "expert_entered"

# Checking facts (S09) ------------------------------------------------------------------------------

# `flagged` is the Points against flag only; `needs_recheck` is "Please check" after a page is read again.
_FACT_COLUMNS = """f.id, f.kind, f.fields, f.value, f.word_ids, f.bbox, f.event_date, f.date_basis, f.status, f.version,
    exists (select 1 from app.fact_flags g where g.fact_id = f.id and g.flagged_for = %(points)s) as flagged,
    exists (select 1 from app.fact_flags g where g.fact_id = f.id and g.flagged_for = %(recheck)s) as needs_recheck,
    exists (select 1 from app.word_corrections c where c.page_id = f.page_id and c.superseded_at is null
            and c.word_ids && f.word_ids) as cites_correction,
    case when f.status <> 'suggested' then (select v.person_id from app.fact_verifications v
        where v.fact_id = f.id and v.action <> 'reset' order by v.seq desc limit 1) end as verified_by,
    case when f.status <> 'suggested' then (select v.at from app.fact_verifications v
        where v.fact_id = f.id and v.action <> 'reset' order by v.seq desc limit 1) end as verified_at"""
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


async def page_reading(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, page_id: uuid.UUID
) -> tuple[dict[str, str], list[Correction]]:
    """The computer's reading of the page's words in reading order, unsealed (kept in memory only, never
    logged), and the expert's current readings of misread words on it (decision 0018)."""
    cur = await conn.execute("select word_id, text from app.ocr_words where page_id = %s order by n", (page_id,))
    words = {
        w: (await crypto.decrypt(conn, case_id, bytes(t), purpose=word_purpose(page_id, w))).decode()
        for w, t in await cur.fetchall()
    }
    corrections = await current_corrections(conn, crypto, case_id, [page_id])
    return words, corrections.get(page_id, [])


async def page_words(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, page_id: uuid.UUID
) -> dict[str, str]:
    """The page's words in reading order with the expert's current readings in place."""
    return apply_corrections(*await page_reading(conn, crypto, case_id, page_id))


def replacing(corrections: list[Correction], new: Correction) -> list[Correction]:
    """The current readings once `new` replaces every one sharing a word with it."""
    return [c for c in corrections if not set(c[0]) & set(new[0])] + [new]


async def fact_reading(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, page_id: uuid.UUID, word_ids: list[str]
) -> dict[str, str]:
    """What the words a fact cites say, in reading order, read as `core/contracts/document_words.cited_words`
    reads them (the anchor rule there)."""
    words, corrections = await page_reading(conn, crypto, case_id, page_id)
    read = apply_corrections(words, corrections, cited=word_ids)
    return {w: t for w, t in read.items() if w in set(word_ids)}


async def corrected_words_near(conn: AsyncConnection, page_id: uuid.UUID, word_ids: list[str]) -> list[str]:
    """`word_ids` and every word of the current readings that share a word with them: the words whose
    reading changes when a new reading of `word_ids` replaces those."""
    cur = await conn.execute(
        "select coalesce(array_agg(distinct w), '{}') from app.word_corrections c, unnest(c.word_ids) w"
        " where c.page_id = %s and c.superseded_at is null and c.word_ids && %s",
        (page_id, word_ids),
    )
    row = await cur.fetchone()
    return sorted(set(word_ids) | set(row[0] if row else []))


async def served_fact_cites(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, word_ids: list[str]) -> bool:
    """Whether a fact cited in a served (or later withdrawn) report cites any of the words (rule 8)."""
    cur = await conn.execute(
        "select exists (select 1 from app.facts f join app.citations c on c.fact_id = f.id"
        " join app.report_versions v on v.id = c.report_version_id"
        " where f.case_id = %s and f.page_id = %s and f.word_ids && %s and v.status in ('served', 'withdrawn'))",
        (case_id, page_id, word_ids),
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def correction_cited(conn: AsyncConnection, page_id: uuid.UUID, word_ids: list[str]) -> uuid.UUID | None:
    """The latest current reading of any of the words, if any."""
    cur = await conn.execute(
        "select id from app.word_corrections where page_id = %s and superseded_at is null and word_ids && %s"
        " order by created_at desc, id desc limit 1",
        (page_id, word_ids),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def save_correction(
    conn: AsyncConnection,
    crypto: CaseCrypto,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    word_ids: list[str],
    text: str,
    person_id: uuid.UUID,
) -> uuid.UUID:
    """Keep the expert's reading of the words, sealed with the case key; it replaces any current reading of
    any of the same words. The computer's reading in `ocr_words` is left as it is."""
    await conn.execute(
        "update app.word_corrections set superseded_at = now()"
        " where page_id = %s and superseded_at is null and word_ids && %s",
        (page_id, word_ids),
    )
    correction_id = uuid.uuid4()
    sealed = await crypto.encrypt(conn, case_id, text.encode(), purpose=correction_purpose(page_id, correction_id))
    await conn.execute(
        "insert into app.word_corrections (id, page_id, word_ids, text, person_id) values (%s, %s, %s, %s, %s)",
        (correction_id, page_id, word_ids, sealed, person_id),
    )
    return correction_id


async def flag_unsupported(
    conn: AsyncConnection,
    crypto: CaseCrypto,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    word_ids: list[str],
    person_id: uuid.UUID,
    *,
    except_fact: uuid.UUID,
) -> None:
    """After a new reading of some words (pass the words of any reading it replaced too), flag "Please check"
    on every other checked fact citing them whose value those words no longer show (rule 2)."""
    cur = await conn.execute(
        "select id, value, word_ids from app.facts where case_id = %s and page_id = %s and id <> %s"
        " and status in ('verified', 'edited') and value is not null and word_ids && %s",
        (case_id, page_id, except_fact, word_ids),
    )
    rows = await cur.fetchall()
    if not rows:
        return
    words, corrections = await page_reading(conn, crypto, case_id, page_id)
    for fact_id, value, cited in rows:
        if not provenance.supported(value, cited, apply_corrections(words, corrections, cited=cited)):
            await set_flag(conn, case_id, fact_id, RECHECK, person_id, on=True)


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
    date_basis: str | None = None,
) -> dict[str, Any] | None:
    """Apply a check if the fact is still at `version` (the database bumps it); None when it moved on."""
    # A date the check leaves alone keeps how it was read; a date taken away takes its basis with it (PV-03).
    basis_sql = ", date_basis = %(date_basis)s" if date_basis is not None else ""
    if date_basis is None and event_date is None:
        basis_sql = ", date_basis = null"
    cur = await conn.execute(
        f"""update app.facts f set status = %(status)s, value = %(value)s, word_ids = %(word_ids)s,
                page_id = %(page)s, bbox = %(bbox)s, fields = %(fields)s, event_date = %(event_date)s{basis_sql}
            where f.id = %(fact)s and f.version = %(version)s returning {_FACT_COLUMNS}""",  # noqa: S608
        {
            "status": status,
            "value": value,
            "word_ids": word_ids,
            "page": page_id,
            "bbox": Jsonb(bbox) if bbox is not None else None,
            "fields": Jsonb(fields),
            "event_date": event_date,
            "date_basis": date_basis,
            "fact": fact_id,
            "version": version,
            **_FLAGS,
        },
    )
    row = await cur.fetchone()
    return _row(cur, row) if row else None


async def _size(conn: AsyncConnection, page_id: uuid.UUID, polygons: dict[str, list[float]]) -> tuple[float, float]:
    """The page's size in its polygons' units: from the page analysis, else the furthest word."""
    cur = await conn.execute("select width, height from app.page_analysis where page_id = %s", (page_id,))
    size = await cur.fetchone()
    width = float(size[0]) if size and size[0] else max((x for p in polygons.values() for x in p[0::2]), default=1.0)
    height = float(size[1]) if size and size[1] else max((y for p in polygons.values() for y in p[1::2]), default=1.0)
    return width or 1.0, height or 1.0


async def word_box(conn: AsyncConnection, page_id: uuid.UUID, word_ids: list[str]) -> dict[str, float] | None:
    """The cited words' box as a share of the page (as slice A boxes facts); None when the words have no
    place on the picture (typed out by the expert)."""
    cur = await conn.execute("select word_id, polygon from app.ocr_words where page_id = %s", (page_id,))
    polygons = {w: [float(v) for v in p] for w, p in await cur.fetchall()}
    cited = [w for w in word_ids if w in polygons]
    if not cited or not any(v for w in cited for v in polygons[w]):
        return None
    return box(cited, polygons, *await _size(conn, page_id, polygons))


async def page_word_boxes(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, page_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Each word on the page in reading order: id, unsealed text (never logged) and its box as a share of
    the page, or None for a word with no place on the picture (typed out by the expert)."""
    cur = await conn.execute(
        "select word_id, text, polygon from app.ocr_words where page_id = %s order by n", (page_id,)
    )
    rows = await cur.fetchall()
    polygons = {w: [float(v) for v in p] for w, _, p in rows}
    width, height = await _size(conn, page_id, polygons)
    return [
        {
            "word_id": w,
            "text": (await crypto.decrypt(conn, case_id, bytes(t), purpose=word_purpose(page_id, w))).decode(),
            "box": box([w], polygons, width, height) if any(polygons[w]) else None,
        }
        for w, t, _ in rows
    ]


async def case_codes(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, Any] | None:
    """The case's pack codes (jurisdiction, case type, specialty)."""
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    return _row(cur, row) if row else None


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
    correction_id: uuid.UUID | None = None,
) -> datetime:
    """Append the check to the case's verification log (the database numbers and hashes it), with the
    expert's reading of misread words it relied on, if any. Returns the time it was logged."""
    cur = await conn.execute(
        """insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value,
               old_fields, new_fields, old_word_ids, new_word_ids, old_page_id, new_page_id, reason, correction_id,
               old_event_date, new_event_date, old_date_basis, new_date_basis)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning at""",
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
            correction_id,
            before.get("event_date"),
            after.get("event_date"),
            before.get("date_basis"),
            after.get("date_basis"),
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


async def set_flag(
    conn: AsyncConnection, case_id: uuid.UUID, fact_id: uuid.UUID, flagged_for: str, person_id: uuid.UUID, on: bool
) -> bool:
    """Puts the flag on or takes it off; whether anything changed. A flag taken off is kept in
    `fact_flag_history` by the database (0228)."""
    if on:
        cur = await conn.execute(
            "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, %s, %s)"
            " on conflict (fact_id, flagged_for) do nothing",
            (case_id, fact_id, flagged_for, person_id),
        )
    else:
        cur = await conn.execute(
            "delete from app.fact_flags where fact_id = %s and flagged_for = %s", (fact_id, flagged_for)
        )
    return cur.rowcount > 0


async def page_id_by_no(conn: AsyncConnection, case_id: uuid.UUID, page_no: int) -> uuid.UUID | None:
    cur = await conn.execute(
        "select id from app.pages where bundle_id = (select bundle_id from app.cases where id = %s) and bundle_page_no "
        "= %s and not quarantined",
        (case_id, page_no),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def unreadable_pages(conn: AsyncConnection, case_id: uuid.UUID) -> list[dict[str, Any]]:
    """The pages the computer couldn't read (not set aside), in page order, with what the expert has done."""
    cur = await conn.execute(
        f"""select p.bundle_page_no as page_no, p.id as page_id, p.source_type, p.handwriting, p.relevance,
                  p.transcribed_by_expert_text is not null as has_transcription, p.needs_clearer_copy,
                  {CONFIRMED_COPY} as is_copy,
                  exists (select 1 from app.page_replacements r where r.page_id = p.id) as has_clearer_copy
           from app.pages p
           where p.bundle_id = (select bundle_id from app.cases where id = %s) and p.unreadable and not p.quarantined
           order by p.bundle_page_no""",  # noqa: S608
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
    """Stores the expert's typing sealed with the case key (never plain, like every OCR word). The page holds
    the latest typing; every typing is also kept, with who and when (PV-12)."""
    sealed = await crypto.encrypt(conn, case_id, text.encode(), purpose=transcription_purpose(page_id))
    await conn.execute(
        "insert into app.page_transcriptions (page_id, person_id, text) values (%s, app.current_person_id(), %s)",
        (page_id, sealed),
    )
    if relevance is not None:
        await change_page(conn, page_id, {"relevance": relevance})
    await conn.execute("update app.pages set transcribed_by_expert_text = %s where id = %s", (sealed, page_id))


def _text(value: Any) -> str | None:
    return None if value is None else str(value).lower()


async def change_page(conn: AsyncConnection, page_id: uuid.UUID, changes: dict[str, Any]) -> bool:
    """Sets the given `relevance` and `needs_clearer_copy` (only those keys), keeping each earlier value with
    who changed it and when (PV-12). Returns whether anything changed."""
    keys = [k for k in changes if k in ("relevance", "needs_clearer_copy")]
    cur = await conn.execute("select relevance, needs_clearer_copy from app.pages where id = %s", (page_id,))
    old = await cur.fetchone()
    assert old is not None
    before = {"relevance": old[0], "needs_clearer_copy": old[1]}
    changed = [k for k in keys if before[k] != changes[k]]
    for k in changed:
        await conn.execute(
            "insert into app.page_changes (page_id, person_id, field, old_value, new_value)"
            " values (%s, app.current_person_id(), %s, %s, %s)",
            (page_id, k, _text(before[k]), _text(changes[k])),
        )
    if keys:
        sets = ", ".join(f"{k} = %({k})s" for k in keys)
        await conn.execute(f"update app.pages set {sets} where id = %(id)s", {**changes, "id": page_id})  # noqa: S608
    return bool(changed)


async def rotate(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, degrees: int) -> int | None:
    """Turns the page clockwise; its new rotation, or None when the case has no such page."""
    cur = await conn.execute(
        "update app.pages set rotation = (rotation + %s) %% 360"
        " where id = %s and bundle_id = (select bundle_id from app.cases where id = %s) and not quarantined returning "
        "rotation",
        (degrees, page_id, case_id),
    )
    row = await cur.fetchone()
    return None if row is None else int(row[0])


async def set_turn(conn: AsyncConnection, page_id: uuid.UUID, degrees: int, person_id: uuid.UUID) -> None:
    """Keeps how the page is turned for reading (one row per page, replaced). Nothing else changes."""
    await conn.execute(
        "insert into app.page_turns (page_id, degrees, person_id) values (%s, %s, %s)"
        " on conflict (page_id) do update set degrees = excluded.degrees, person_id = excluded.person_id,"
        " updated_at = now()",
        (page_id, degrees, person_id),
    )


async def pages_to_type(conn: AsyncConnection, case_ids: list[uuid.UUID]) -> list[tuple[uuid.UUID, int]]:
    """(case, unreadable pages not yet typed out or sent for a clearer copy) where there are any."""
    cur = await conn.execute(
        f"""select c.id, count(*)::int from app.pages p
           join app.cases c on c.bundle_id = p.bundle_id
           where c.id = any(%s) and p.unreadable and not p.quarantined
             and p.transcribed_by_expert_text is null and not p.needs_clearer_copy
             and p.relevance is distinct from 'nothing' and not {CONFIRMED_COPY}
             and not exists (select 1 from app.page_replacements r where r.page_id = p.id)
           group by c.id""",  # noqa: S608
        (case_ids,),
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]


# Finding words in the bundle (S09) -------------------------------------------------------------------


async def searchable_pages(conn: AsyncConnection, case_id: uuid.UUID) -> dict[uuid.UUID, tuple[int, uuid.UUID]]:
    """Each page that is not set aside: page id -> (page number, document id)."""
    cur = await conn.execute(
        "select id, bundle_page_no, document_id from app.pages where bundle_id = (select bundle_id from app.cases "
        "where id = %s) and not quarantined",
        (case_id,),
    )
    return {p: (int(n), d) for p, n, d in await cur.fetchall()}


async def search_version(conn: AsyncConnection, case_id: uuid.UUID) -> tuple[object, ...]:
    """What the case's searchable words depend on, cheaply (no word is read): changes whenever pages are added or
    set aside, the words of a page are read again (OCR or the expert's own typing), or the expert's readings of
    misread words change. The search cache is valid only while this is unchanged."""
    cur = await conn.execute(
        "select count(*), count(*) filter (where p.text_source = 'expert'), coalesce(sum(p.ocr_confidence), 0)::float8,"
        " coalesce(sum(a.word_count), 0), max(a.updated_at)"
        " from app.pages p left join app.page_analysis a on a.page_id = p.id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and not p.quarantined",
        (case_id,),
    )
    pages = await cur.fetchone()
    cur = await conn.execute(
        "select count(*), max(greatest(c.created_at, coalesce(c.superseded_at, c.created_at)))"
        " from app.word_corrections c join app.pages p on p.id = c.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and not p.quarantined",
        (case_id,),
    )
    corrections = await cur.fetchone()
    return (*(pages or ()), *(corrections or ()))


SEARCH_BATCH_PAGES = 200


async def search_words(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> CaseWords:
    """Every searchable word of the case as the expert reads it, opened a batch of pages at a time (each batch's
    opening runs in a thread), with each word's polygon and each page's size. Patient text: search cache only."""
    pages = await searchable_pages(conn, case_id)
    order = sorted(pages, key=lambda p: pages[p][0])
    out: dict[uuid.UUID, PageWords] = {}
    for start in range(0, len(order), SEARCH_BATCH_PAGES):
        batch = order[start : start + SEARCH_BATCH_PAGES]
        cur = await conn.execute(
            "select page_id, word_id, text, polygon from app.ocr_words where page_id = any (%s) order by page_id, n",
            (batch,),
        )
        rows = await cur.fetchall()
        texts = await open_words(conn, crypto, case_id, [(r[0], r[1], r[2]) for r in rows])
        polygons: dict[uuid.UUID, dict[str, list[float]]] = {}
        by_page: dict[uuid.UUID, list[tuple[str, str]]] = {}
        for (page, word, _, polygon), text in zip(rows, texts, strict=True):
            by_page.setdefault(page, []).append((word, text))
            polygons.setdefault(page, {})[word] = [float(v) for v in polygon or []]
        corrections = await current_corrections(conn, crypto, case_id, by_page)
        for page, words in by_page.items():
            read = read_as_corrected(words, corrections.get(page, []))
            places = polygons[page]
            poly: array = array("f")  # type: ignore[type-arg]
            for word, _ in read:
                poly.extend(places[word] if len(places[word]) == 8 else [0.0] * 8)
            out[page] = PageWords(ids=[w for w, _ in read], texts=[t.lower() for _, t in read], poly=poly)
    cur = await conn.execute(
        "select a.page_id, a.width, a.height from app.page_analysis a join app.pages p on p.id = a.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and a.width > 0 and a.height > 0",
        (case_id,),
    )
    sizes = {page: (float(wd), float(ht)) for page, wd, ht in await cur.fetchall() if page in out}
    return CaseWords(pages=out, sizes=sizes)


# The person's own bookmarks and notes (S09) ---------------------------------------------------------


def note_purpose(page_id: uuid.UUID, person_id: uuid.UUID) -> str:
    """The purpose each private note is sealed under."""
    return f"page_notes/{page_id}/{person_id}"


async def keep_note(
    conn: AsyncConnection,
    crypto: CaseCrypto,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    person_id: uuid.UUID,
    bookmarked: bool,
    note: str,
) -> Any:
    """Keeps the person's bookmark and note for the page (one row, replaced); clearing both removes it.
    Returns when it was kept (None once removed). The note is sealed and never logged."""
    if not bookmarked and not note:
        await conn.execute("delete from app.page_notes where page_id = %s and person_id = %s", (page_id, person_id))
        return None
    sealed = (
        await crypto.encrypt(conn, case_id, note.encode(), purpose=note_purpose(page_id, person_id)) if note else None
    )
    cur = await conn.execute(
        "insert into app.page_notes (page_id, person_id, bookmarked, note) values (%s, %s, %s, %s)"
        " on conflict (page_id, person_id) do update set bookmarked = excluded.bookmarked, note = excluded.note,"
        " updated_at = now() returning updated_at",
        (page_id, person_id, bookmarked, sealed),
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0]


async def my_notes(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, person_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The person's own bookmarks and notes on the case, in page order, unsealed (never logged)."""
    cur = await conn.execute(
        "select n.page_id, p.bundle_page_no, n.bookmarked, n.note, n.updated_at from app.page_notes n"
        " join app.pages p on p.id = n.page_id where p.bundle_id = (select bundle_id from app.cases where id = %s) and "
        "n.person_id = %s"
        " order by p.bundle_page_no",
        (case_id, person_id),
    )
    out = []
    for page_id, page_no, bookmarked, sealed, updated_at in await cur.fetchall():
        note = ""
        if sealed is not None:
            plain = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=note_purpose(page_id, person_id))
            note = plain.decode()
        out.append({"page_no": page_no, "bookmarked": bookmarked, "note": note, "updated_at": updated_at})
    return out


_TO_READ = (
    "p.bundle_id = (select bundle_id from app.cases where id = %s) and not p.quarantined"
    f" and not {CONFIRMED_COPY} and p.superseded_by_page_id is null"
)  # the same pages `transcript._pages_to_read` counts


async def page_pictures(conn: AsyncConnection, case_id: uuid.UUID) -> list[tuple[int, str | None]]:
    """(bundle page number, picture path or None) for each page not set aside, in page order."""
    cur = await conn.execute(
        f"select p.bundle_page_no, p.image_path from app.pages p where {_TO_READ} order by p.bundle_page_no",  # noqa: S608
        (case_id,),
    )
    return [(int(r[0]), r[1]) for r in await cur.fetchall()]


async def page_counts(conn: AsyncConnection, case_id: uuid.UUID) -> tuple[int, int]:
    """(pages not set aside, pages set aside): copies, replaced and quarantined pages are set aside."""
    cur = await conn.execute(
        f"select count(*) filter (where {_TO_READ}), count(*) filter (where not ({_TO_READ}))"  # noqa: S608
        " from app.pages p where p.bundle_id = (select bundle_id from app.cases where id = %s)",
        (case_id, case_id, case_id),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


async def pages_set_aside(conn: AsyncConnection, case_id: uuid.UUID) -> list[tuple[int, str]]:
    """(bundle page number, reason key) of each page `_TO_READ` leaves out. Held back wins over replaced, and
    replaced over copy, so each page is listed once."""
    cur = await conn.execute(
        "select p.bundle_page_no,"
        " case when p.quarantined then 'reason_held'"
        "      when p.superseded_by_page_id is not null then 'reason_replaced'"
        "      else 'reason_copy' end"
        f" from app.pages p where p.bundle_id = (select bundle_id from app.cases where id = %s) and not ({_TO_READ})"  # noqa: S608
        " order by p.bundle_page_no",
        (case_id, case_id),
    )
    return [(int(r[0]), str(r[1])) for r in await cur.fetchall()]
