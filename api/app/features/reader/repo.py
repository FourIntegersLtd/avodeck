"""SQL for slice B's tables (`page_reads`, `fact_verifications`, `fact_flags`) and its writes on `pages` and `facts`.

Every function runs on the request's or job's connection, so row-level security applies (ADR 0013).
"""

from __future__ import annotations

import uuid
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
    word_purpose,
)
from app.core.contracts.fact_flags import POINTS, RECHECK
from app.core.contracts.pipeline import transcription_purpose
from app.core.crypto import CaseCrypto


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
        f"""select {_PAGE_COLUMNS} {_DETAIL_COLUMNS}, p.image_path, p.duplicate_of_page_id
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


async def progress(conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> tuple[int, int, int]:
    """(pages read, pages, seconds left). Each unread page takes the person's median dwell for its kind of
    source, else their median over every page, else `DEFAULT_SECONDS_PER_PAGE`."""
    cur = await conn.execute(
        """with shown as (select p.id, p.source_type from app.pages p where p.bundle_id = (select bundle_id from app.cases where id = %(case)s) and not p.quarantined),
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


def _row(cur: Any, row: tuple[Any, ...]) -> dict[str, Any]:
    return {d.name: v for d, v in zip(cur.description, row, strict=True)}


# Checking facts (S09) ------------------------------------------------------------------------------

# `flagged` is the Points against flag only; `needs_recheck` is "Please check" after a page is read again.
_FACT_COLUMNS = """f.id, f.kind, f.fields, f.value, f.word_ids, f.bbox, f.event_date, f.status, f.version,
    exists (select 1 from app.fact_flags g where g.fact_id = f.id and g.flagged_for = %(points)s) as flagged,
    exists (select 1 from app.fact_flags g where g.fact_id = f.id and g.flagged_for = %(recheck)s) as needs_recheck,
    exists (select 1 from app.word_corrections c where c.page_id = f.page_id and c.superseded_at is null
            and c.word_ids && f.word_ids) as cites_correction"""
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
    basis_sql = ", date_basis = %(date_basis)s" if date_basis is not None else ""
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
) -> None:
    """Append the check to the case's verification log (the database numbers and hashes it), with the
    expert's reading of misread words it relied on, if any."""
    await conn.execute(
        """insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value,
               old_fields, new_fields, old_word_ids, new_word_ids, old_page_id, new_page_id, reason, correction_id)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
        "select id from app.pages where bundle_id = (select bundle_id from app.cases where id = %s) and bundle_page_no = %s and not quarantined", (case_id, page_no)
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def unreadable_pages(conn: AsyncConnection, case_id: uuid.UUID) -> list[dict[str, Any]]:
    """The pages the computer couldn't read (not set aside), in page order, with what the expert has done."""
    cur = await conn.execute(
        """select p.bundle_page_no as page_no, p.id as page_id, p.source_type, p.handwriting, p.relevance,
                  p.transcribed_by_expert_text is not null as has_transcription, p.needs_clearer_copy,
                  p.duplicate_of_page_id is not null as is_copy,
                  exists (select 1 from app.page_replacements r where r.page_id = p.id) as has_clearer_copy
           from app.pages p where p.bundle_id = (select bundle_id from app.cases where id = %s) and p.unreadable and not p.quarantined
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
        " where id = %s and bundle_id = (select bundle_id from app.cases where id = %s) and not quarantined returning rotation",
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
        """select c.id, count(*)::int from app.pages p
           join app.cases c on c.bundle_id = p.bundle_id
           where c.id = any(%s) and p.unreadable and not p.quarantined
             and p.transcribed_by_expert_text is null and not p.needs_clearer_copy
             and p.relevance is distinct from 'nothing' and p.duplicate_of_page_id is null
             and not exists (select 1 from app.page_replacements r where r.page_id = p.id)
           group by c.id""",
        (case_ids,),
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]


# Finding words in the bundle (S09) -------------------------------------------------------------------


async def searchable_pages(conn: AsyncConnection, case_id: uuid.UUID) -> dict[uuid.UUID, tuple[int, uuid.UUID]]:
    """Each page that is not set aside: page id -> (page number, document id)."""
    cur = await conn.execute(
        "select id, bundle_page_no, document_id from app.pages where bundle_id = (select bundle_id from app.cases where id = %s) and not quarantined",
        (case_id,),
    )
    return {p: (int(n), d) for p, n, d in await cur.fetchall()}


async def word_places(
    conn: AsyncConnection, case_id: uuid.UUID
) -> tuple[dict[uuid.UUID, dict[str, list[float]]], dict[uuid.UUID, tuple[float, float]]]:
    """Every word's polygon by page (pages not set aside), and each page's size from its analysis."""
    cur = await conn.execute(
        "select w.page_id, w.word_id, w.polygon from app.ocr_words w join app.pages p on p.id = w.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and not p.quarantined",
        (case_id,),
    )
    polygons: dict[uuid.UUID, dict[str, list[float]]] = {}
    for page, word, polygon in await cur.fetchall():
        polygons.setdefault(page, {})[word] = [float(v) for v in polygon or []]
    cur = await conn.execute(
        "select a.page_id, a.width, a.height from app.page_analysis a join app.pages p on p.id = a.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and a.width > 0 and a.height > 0",
        (case_id,),
    )
    sizes = {page: (float(wd), float(ht)) for page, wd, ht in await cur.fetchall()}
    return polygons, sizes


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
        " join app.pages p on p.id = n.page_id where p.bundle_id = (select bundle_id from app.cases where id = %s) and n.person_id = %s"
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
