"""SQL for slice A's tables (and the page rows the pipeline creates)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.features.pipeline import gaps

RECORDS_KIND = "records"  # the core document kind that becomes numbered pages


@dataclass(frozen=True)
class Document:
    id: uuid.UUID
    storage_path: str
    filename: str = ""


async def unscanned_documents(conn: AsyncConnection, case_id: uuid.UUID) -> list[Document]:
    cur = await conn.execute(
        "select d.id, d.storage_path from app.documents d"
        " where d.case_id = %s and d.storage_path is not null"
        " and not exists (select 1 from app.document_scans s where s.document_id = d.id)"
        " order by d.created_at, d.id",
        (case_id,),
    )
    return [Document(r[0], r[1]) for r in await cur.fetchall()]


async def record_scan(
    conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID, clean: bool, signature: str | None
) -> bool:
    cur = await conn.execute(
        "insert into app.document_scans (document_id, case_id, clean, signature) values (%s, %s, %s, %s)"
        " on conflict (document_id) do nothing returning document_id",
        (document_id, case_id, clean, signature),
    )
    return await cur.fetchone() is not None


async def clean_unsplit(conn: AsyncConnection, case_id: uuid.UUID) -> list[Document]:
    cur = await conn.execute(
        "select d.id, d.storage_path, d.filename from app.documents d"
        " join app.document_scans s on s.document_id = d.id"
        " where d.case_id = %s and s.clean and s.split_at is null and d.kind = %s"
        " order by d.created_at, d.id",
        (case_id, RECORDS_KIND),
    )
    return [Document(r[0], r[1], r[2]) for r in await cur.fetchall()]


async def add_pages(conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID, count: int) -> bool:
    """Number the document's pages after the case's last page, once. False if already split."""
    cur = await conn.execute(
        "select 1 from app.document_scans where document_id = %s and split_at is null for update", (document_id,)
    )
    if await cur.fetchone() is None:
        return False
    cur = await conn.execute("select coalesce(max(bundle_page_no), 0) from app.pages where case_id = %s", (case_id,))
    row = await cur.fetchone()
    start = int(row[0]) if row else 0
    await conn.execute(
        "insert into app.pages (case_id, document_id, bundle_page_no)"
        " select %s, %s, %s + g from generate_series(1, %s) g",
        (case_id, document_id, start, count),
    )
    await conn.execute("update app.documents set pages = %s where id = %s", (count, document_id))
    await conn.execute("update app.document_scans set split_at = now() where document_id = %s", (document_id,))
    return True


async def mark_unopened(conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID, reason: str) -> None:
    """F2: the file could not be opened (`locked` or `damaged`). It gets no pages and is not tried again."""
    await conn.execute("update app.documents set unreadable_reason = %s where id = %s", (reason, document_id))
    await add_pages(conn, case_id, document_id, 0)


async def unopened_documents(conn: AsyncConnection, case_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    """Files that could not be opened, as (id, filename), oldest first."""
    cur = await conn.execute(
        "select id, filename from app.documents where case_id = %s and unreadable_reason is not null"
        " order by created_at, id",
        (case_id,),
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]


@dataclass(frozen=True)
class PageRow:
    id: uuid.UUID
    document_id: uuid.UUID
    bundle_page_no: int
    first_page_no: int
    rotation: int = 0


async def pages_without_image(conn: AsyncConnection, case_id: uuid.UUID) -> list[PageRow]:
    cur = await conn.execute(
        "select p.id, p.document_id, p.bundle_page_no,"
        " (select min(q.bundle_page_no) from app.pages q where q.document_id = p.document_id), p.rotation"
        " from app.pages p where p.case_id = %s and p.image_path is null order by p.bundle_page_no",
        (case_id,),
    )
    return [PageRow(r[0], r[1], r[2], r[3], r[4]) for r in await cur.fetchall()]


async def read_again(conn: AsyncConnection, case_id: uuid.UUID, page_ids: list[uuid.UUID]) -> None:
    """The named pages (not quarantined) get new pictures and their words read again on the next run."""
    await conn.execute(
        "update app.pages set image_path = null, text_source = null"
        " where case_id = %s and id = any(%s) and not quarantined",
        (case_id, page_ids),
    )


async def read_typed_again(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> bool:
    """The page's words are taken again from the expert's typed-out text on the next run."""
    cur = await conn.execute(
        "update app.pages set text_source = null where case_id = %s and id = %s and not quarantined"
        " and transcribed_by_expert_text is not null returning id",
        (case_id, page_id),
    )
    return await cur.fetchone() is not None


@dataclass(frozen=True)
class ReadingState:
    rotation: int
    typed: bytes | None  # sealed: open with `transcription_purpose(page_id)`
    unreadable: bool
    handwriting: bool
    straightened: bool = False


async def reading_state(conn: AsyncConnection, page_id: uuid.UUID) -> ReadingState:
    cur = await conn.execute(
        "select rotation, transcribed_by_expert_text, unreadable, handwriting, straightened from app.pages"
        " where id = %s",
        (page_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return ReadingState(
        int(row[0]), None if row[1] is None else bytes(row[1]), bool(row[2]), bool(row[3]), bool(row[4])
    )


async def set_straightened(conn: AsyncConnection, page_id: uuid.UUID, rotation: int) -> None:
    """The page is stored `rotation` degrees clockwise because its scan came in turned; it keeps its number."""
    await conn.execute("update app.pages set rotation = %s, straightened = true where id = %s", (rotation, page_id))


async def straightened_count(conn: AsyncConnection, case_id: uuid.UUID) -> int:
    cur = await conn.execute(
        "select count(*) from app.pages where case_id = %s and straightened and not quarantined", (case_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def checked_facts(conn: AsyncConnection, page_id: uuid.UUID) -> list[tuple[uuid.UUID, list[str], str | None]]:
    """(fact, cited words, value) for the page's checked facts."""
    cur = await conn.execute(
        "select id, word_ids, value from app.facts where page_id = %s and status in ('verified', 'edited')"
        " order by created_at",
        (page_id,),
    )
    return [(r[0], list(r[1]), r[2]) for r in await cur.fetchall()]


async def settle_facts(
    conn: AsyncConnection, page_id: uuid.UUID, moved: dict[uuid.UUID, tuple[list[str], dict[str, float] | None]]
) -> None:
    """After a page is read again: checked facts move onto their words in the new reading, unchecked
    suggestions are superseded (rejected), and the page is extracted again."""
    for fact_id, (word_ids, bbox) in moved.items():
        await conn.execute(
            "update app.facts set word_ids = %s, bbox = %s where id = %s",
            (word_ids, Jsonb(bbox) if bbox is not None else None, fact_id),
        )
    await conn.execute(
        "update app.facts set status = 'rejected' where page_id = %s and status = 'suggested'", (page_id,)
    )
    await conn.execute("update app.page_analysis set extracted_at = null where page_id = %s", (page_id,))


async def document_file(conn: AsyncConnection, document_id: uuid.UUID) -> tuple[str, str]:
    """Where a document is kept, and its filename."""
    cur = await conn.execute("select storage_path, filename from app.documents where id = %s", (document_id,))
    row = await cur.fetchone()
    assert row is not None and row[0]
    return str(row[0]), str(row[1])


# --- pages in another language (F37) --------------------------------------------------------------


async def set_language(conn: AsyncConnection, page_id: uuid.UUID, code: str | None) -> None:
    await conn.execute("update app.pages set language = %s where id = %s", (code, page_id))


async def has_page_languages(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    cur = await conn.execute(
        "select exists (select 1 from app.pages where case_id = %s and language is not null)", (case_id,)
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def foreign_pages(conn: AsyncConnection, case_id: uuid.UUID, spoken: str) -> list[tuple[uuid.UUID, int, str]]:
    """Pages whose language is known and is not the pack's, in page order."""
    cur = await conn.execute(
        "select id, bundle_page_no, language from app.pages"
        " where case_id = %s and language is not null and language <> %s order by bundle_page_no",
        (case_id, spoken),
    )
    return [(r[0], r[1], r[2]) for r in await cur.fetchall()]


# --- files that are not PDFs (F36) -----------------------------------------------------------------


async def part_depth(conn: AsyncConnection, document_id: uuid.UUID) -> int:
    """How many emails or zips the document sits inside."""
    cur = await conn.execute(
        "with recursive up(id, depth) as (select %s::uuid, 0 union all"
        " select p.parent_document_id, up.depth + 1 from app.document_parts p join up on p.document_id = up.id"
        " where up.depth < 50) select max(depth) from up",
        (document_id,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def add_part(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    parent_id: uuid.UUID,
    part_no: int,
    *,
    document_id: uuid.UUID,
    filename: str,
    media_type: str,
    sha256: str,
    storage_path: str,
) -> bool:
    """A file inside an email or a zip becomes a document of its own, once. False if already made."""
    cur = await conn.execute(
        "select 1 from app.document_parts where parent_document_id = %s and part_no = %s", (parent_id, part_no)
    )
    if await cur.fetchone() is not None:
        return False
    await conn.execute(
        "insert into app.documents (id, case_id, kind, filename, storage_path, sha256, received_on, obtained_how,"
        " bundle_version, uploaded_by)"
        " select %s, case_id, kind, %s, %s, %s, received_on, obtained_how, bundle_version, uploaded_by"
        " from app.documents where id = %s",
        (document_id, filename[:255], storage_path, sha256, parent_id),
    )
    await conn.execute(
        "insert into app.document_scans (document_id, case_id, clean) values (%s, %s, true)", (document_id, case_id)
    )
    await conn.execute(
        "insert into app.document_parts (case_id, parent_document_id, document_id, part_no, media_type)"
        " values (%s, %s, %s, %s, %s)",
        (case_id, parent_id, document_id, part_no, media_type),
    )
    return True


async def set_media_type(conn: AsyncConnection, document_id: uuid.UUID, media_type: str) -> None:
    await conn.execute(
        "update app.document_scans set media_type = %s where document_id = %s", (media_type, document_id)
    )


async def add_exhibit(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    media_type: str,
    size: int,
    sha256: str,
    storage_path: str,
) -> None:
    await conn.execute(
        "insert into app.exhibits (case_id, document_id, media_type, size, sha256, storage_path)"
        " values (%s, %s, %s, %s, %s, %s) on conflict (document_id) do nothing",
        (case_id, document_id, media_type, size, sha256, storage_path),
    )


@dataclass(frozen=True)
class ExhibitRow:
    id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    media_type: str
    size: int
    part_of: uuid.UUID | None
    created_at: Any


async def exhibits(conn: AsyncConnection, case_id: uuid.UUID) -> list[ExhibitRow]:
    cur = await conn.execute(
        "select e.id, e.document_id, d.filename, e.media_type, e.size, p.parent_document_id, e.created_at"
        " from app.exhibits e join app.documents d on d.id = e.document_id"
        " left join app.document_parts p on p.document_id = e.document_id"
        " where e.case_id = %s order by e.created_at, d.filename",
        (case_id,),
    )
    return [ExhibitRow(*r) for r in await cur.fetchall()]


async def set_image(conn: AsyncConnection, page_id: uuid.UUID, image_path: str) -> None:
    await conn.execute("update app.pages set image_path = %s where id = %s", (image_path, page_id))


async def pages_without_words(conn: AsyncConnection, case_id: uuid.UUID) -> list[uuid.UUID]:
    cur = await conn.execute(
        "select id from app.pages where case_id = %s and text_source is null and image_path is not null"
        " and not quarantined order by bundle_page_no",
        (case_id,),
    )
    return [r[0] for r in await cur.fetchall()]


async def pages_without_language(conn: AsyncConnection, case_id: uuid.UUID, min_words: int) -> list[uuid.UUID]:
    """Pages already read that have enough words to name a language but no language yet."""
    cur = await conn.execute(
        "select p.id from app.pages p where p.case_id = %s and p.text_source is not null and p.language is null"
        " and not p.quarantined and (select count(*) from app.ocr_words w where w.page_id = p.id) >= %s"
        " order by p.bundle_page_no",
        (case_id, min_words),
    )
    return [r[0] for r in await cur.fetchall()]


WordRow = tuple[uuid.UUID, uuid.UUID, int, str, bytes, list[float], float, bool, bool, str]


async def store_words(
    conn: AsyncConnection,
    page_id: uuid.UUID,
    rows: list[WordRow],
    *,
    source: str,
    confidence: float,
    handwriting: bool,
    unreadable: bool,
    size: tuple[float, float] | None = None,
) -> None:
    await conn.execute("delete from app.ocr_words where page_id = %s", (page_id,))
    # Word ids are places on the page: the expert's readings of the old words (slice B, decision 0018)
    # would sit on the new ones, so each is marked replaced with the words, in the same transaction.
    await conn.execute(
        "update app.word_corrections set superseded_at = now() where page_id = %s and superseded_at is null",
        (page_id,),
    )
    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into app.ocr_words"
            " (page_id, case_id, n, word_id, text, polygon, confidence, handwritten, struck, source)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    await conn.execute(
        "update app.pages set text_source = %s, ocr_confidence = %s, handwriting = %s, unreadable = %s where id = %s",
        (source, confidence, handwriting, unreadable, page_id),
    )
    if size is not None:
        await conn.execute(
            "insert into app.page_analysis (page_id, case_id, stage, word_count, width, height)"
            " select id, case_id, 'ocr', %s, %s, %s from app.pages where id = %s"
            " on conflict (page_id) do update set stage = excluded.stage, word_count = excluded.word_count,"
            " width = excluded.width, height = excluded.height, updated_at = now()",
            (len(rows), size[0], size[1], page_id),
        )


async def case_codes(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, str]:
    """The case's pack codes (`jurisdiction`, `case_type`, `specialty`)."""
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    if row is None:
        raise LookupError("case not found")
    return {"jurisdiction": row[0], "case_type": row[1], "specialty": row[2]}


async def pages_to_extract(conn: AsyncConnection, case_id: uuid.UUID) -> list[uuid.UUID]:
    """Pages with their words read, not set aside as copies, and not yet extracted."""
    cur = await conn.execute(
        "select p.id from app.pages p left join app.page_analysis a on a.page_id = p.id"
        " where p.case_id = %s and p.text_source is not null and p.duplicate_of_page_id is null"
        " and not p.quarantined and a.extracted_at is null order by p.bundle_page_no",
        (case_id,),
    )
    return [r[0] for r in await cur.fetchall()]


@dataclass(frozen=True)
class StoredWord:
    word_id: str
    sealed: bytes
    polygon: list[float]
    struck: bool
    handwritten: bool = False


async def words_of(conn: AsyncConnection, page_id: uuid.UUID) -> list[StoredWord]:
    cur = await conn.execute(
        "select word_id, text, polygon, struck, handwritten from app.ocr_words where page_id = %s order by n",
        (page_id,),
    )
    return [StoredWord(r[0], bytes(r[1]), list(r[2]), bool(r[3]), bool(r[4])) for r in await cur.fetchall()]


async def previous_page_words(
    conn: AsyncConnection, page_id: uuid.UUID, limit: int
) -> tuple[uuid.UUID, list[StoredWord]] | None:
    """The first `limit` words of the page before this one in the same document, if it has any."""
    cur = await conn.execute(
        "select q.id from app.pages p join app.pages q on q.document_id = p.document_id"
        " and q.bundle_page_no = p.bundle_page_no - 1 where p.id = %s and not q.quarantined",
        (page_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    cur = await conn.execute(
        "select word_id, text, polygon, struck, handwritten from app.ocr_words where page_id = %s"
        " and not struck order by n limit %s",
        (row[0], limit),
    )
    words = [StoredWord(r[0], bytes(r[1]), list(r[2]), bool(r[3]), bool(r[4])) for r in await cur.fetchall()]
    return (row[0], words) if words else None


async def page_size(conn: AsyncConnection, page_id: uuid.UUID) -> tuple[float, float] | None:
    cur = await conn.execute("select width, height from app.page_analysis where page_id = %s", (page_id,))
    row = await cur.fetchone()
    return (float(row[0]), float(row[1])) if row and row[0] and row[1] else None


@dataclass(frozen=True)
class NewFact:
    kind: str
    fields: dict[str, Any]
    value: str
    word_ids: list[str]
    bbox: dict[str, float]
    event_date: dt.date | None = None
    date_basis: str | None = None


@dataclass(frozen=True)
class NewEntry:
    kind: str
    first_n: int
    last_n: int
    author_role: str | None = None
    specialty: str | None = None
    event_date: dt.date | None = None
    date_basis: str | None = None


async def _put_entries(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    entries: list[NewEntry],
    rejected: int,
    version: str,
) -> None:
    """Replaces the page's entries and marks them read, so a rerun gives the same rows (never doubled)."""
    await conn.execute("delete from app.record_entries where page_id = %s", (page_id,))
    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into app.record_entries (page_id, case_id, kind, first_n, last_n, author_role, specialty,"
            " event_date, date_basis, extraction_version) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    page_id,
                    case_id,
                    e.kind,
                    e.first_n,
                    e.last_n,
                    e.author_role,
                    e.specialty,
                    e.event_date,
                    e.date_basis,
                    version,
                )
                for e in entries
            ],
        )
    await conn.execute(
        "update app.page_analysis set entries_extracted_at = now(), entries_failed_at = null, entries_rejected = %s,"
        " updated_at = now()"
        " where page_id = %s",
        (rejected, page_id),
    )


async def store_entries(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    entries: list[NewEntry],
    *,
    rejected: int,
    version: str,
) -> None:
    """The entries job's write: entries only; the page's facts and notes are left as they are."""
    await _put_entries(conn, case_id, page_id, entries, rejected, version)


async def pages_to_read_entries(conn: AsyncConnection, case_id: uuid.UUID) -> list[uuid.UUID]:
    """Extracted pages whose entries have not been read (a case read before entries existed)."""
    cur = await conn.execute(
        "select p.id from app.pages p join app.page_analysis a on a.page_id = p.id"
        " where p.case_id = %s and a.extracted_at is not null and a.entries_extracted_at is null"
        " and a.entries_failed_at is null and not p.quarantined order by p.bundle_page_no",
        (case_id,),
    )
    return [r[0] for r in await cur.fetchall()]


async def leave_entries_for_person(conn: AsyncConnection, page_id: uuid.UUID) -> None:
    """The entries could not be read after the retry: not marked read, flagged `needs_human`."""
    await conn.execute(
        "update app.page_analysis set needs_human = true, reason = coalesce(reason, 'invalid_output'),"
        " entries_failed_at = now(), updated_at = now() where page_id = %s",
        (page_id,),
    )


async def has_entries(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    cur = await conn.execute("select 1 from app.record_entries where case_id = %s limit 1", (case_id,))
    return await cur.fetchone() is not None


async def store_extraction(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    *,
    source_type: str | None,
    facts: list[NewFact],
    sealed: bytes | None,
    needs_human: bool,
    reason: str | None,
    task: str,
    model_id: str,
    prompt_version: str,
    printed_dates: list[dt.date] | None = None,
    availability_evidence: list[dict[str, Any]] | None = None,
    entries: list[NewEntry] | None = None,
    entries_rejected: int = 0,
) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into app.facts (case_id, page_id, kind, fields, value, word_ids, bbox, event_date, date_basis,"
            " model_task, model_id, prompt_version) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    case_id,
                    page_id,
                    f.kind,
                    Jsonb(f.fields),
                    f.value,
                    f.word_ids,
                    Jsonb(f.bbox),
                    f.event_date,
                    f.date_basis,
                    task,
                    model_id,
                    prompt_version,
                )
                for f in facts
            ],
        )
    if source_type is not None:
        await conn.execute("update app.pages set source_type = %s where id = %s", (source_type, page_id))
    await conn.execute(
        "insert into app.page_analysis (page_id, case_id, stage, extraction, needs_human, reason, model_id,"
        " printed_dates, availability_evidence, extracted_at)"
        " values (%s, %s, 'extract', %s, %s, %s, %s, %s, %s, now())"
        " on conflict (page_id) do update set stage = excluded.stage, extraction = excluded.extraction,"
        " needs_human = excluded.needs_human, reason = excluded.reason, model_id = excluded.model_id,"
        " printed_dates = excluded.printed_dates, availability_evidence = excluded.availability_evidence,"
        " extracted_at = excluded.extracted_at, updated_at = now()",
        (
            page_id,
            case_id,
            sealed,
            needs_human,
            reason,
            model_id,
            printed_dates or [],
            Jsonb(availability_evidence or []),
        ),
    )
    if entries is not None:
        await _put_entries(conn, case_id, page_id, entries, entries_rejected, prompt_version)
    else:  # the model gave no usable answer: the page stays unread, for a person
        await conn.execute("update app.page_analysis set entries_failed_at = now() where page_id = %s", (page_id,))


# Copies set aside (increment 5) -----------------------------------------------------------------


async def pages_to_hash(conn: AsyncConnection, case_id: uuid.UUID) -> list[uuid.UUID]:
    """Pages with their words read and no picture or word hash yet."""
    cur = await conn.execute(
        "select a.page_id from app.page_analysis a join app.pages p on p.id = a.page_id"
        " where a.case_id = %s and a.word_count is not null and a.phash is null and not p.quarantined"
        " order by p.bundle_page_no",
        (case_id,),
    )
    return [r[0] for r in await cur.fetchall()]


async def store_hashes(
    conn: AsyncConnection, page_id: uuid.UUID, picture: int, words: list[int], typed_words: int
) -> None:
    await conn.execute(
        "update app.page_analysis set phash = %s, minhash = %s, typed_words = %s, updated_at = now()"
        " where page_id = %s",
        (picture, words, typed_words, page_id),
    )


@dataclass(frozen=True)
class CopyState:
    page_id: uuid.UUID
    page_no: int
    picture: int | None
    words: list[int] | None
    typed_words: int
    checked: bool
    copy_of: uuid.UUID | None
    status: str | None


async def copy_states(conn: AsyncConnection, case_id: uuid.UUID) -> list[CopyState]:
    cur = await conn.execute(
        "select p.id, p.bundle_page_no, a.phash, a.minhash, coalesce(a.typed_words, 0),"
        " a.duplicate_checked_at is not null, p.duplicate_of_page_id, a.duplicate_status"
        " from app.pages p join app.page_analysis a on a.page_id = p.id"
        " where p.case_id = %s and not p.quarantined order by p.bundle_page_no",
        (case_id,),
    )
    return [
        CopyState(r[0], r[1], r[2], list(r[3]) if r[3] is not None else None, r[4], r[5], r[6], r[7])
        for r in await cur.fetchall()
    ]


async def set_aside(conn: AsyncConnection, copies: dict[uuid.UUID, uuid.UUID]) -> None:
    """Each copy points at the page it copies; nothing on it is removed."""
    for page_id, original in copies.items():
        await conn.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (original, page_id))
        await conn.execute(
            "update app.page_analysis set duplicate_status = 'suggested', updated_at = now() where page_id = %s",
            (page_id,),
        )


async def mark_checked(conn: AsyncConnection, page_ids: list[uuid.UUID]) -> None:
    await conn.execute(
        "update app.page_analysis set duplicate_checked_at = now(), updated_at = now()"
        " where page_id = any(%s) and duplicate_checked_at is null",
        (page_ids,),
    )


@dataclass(frozen=True)
class Copy:
    page_id: uuid.UUID
    page_no: int
    copy_of_page_id: uuid.UUID | None
    copy_of_page_no: int | None
    status: str
    version: int


_COPY = (
    "select p.id, p.bundle_page_no, o.id, o.bundle_page_no, coalesce(a.duplicate_status, 'suggested'),"
    " coalesce(a.duplicate_version, 0)"
    " from app.pages p left join app.pages o on o.id = p.duplicate_of_page_id"
    " left join app.page_analysis a on a.page_id = p.id"
)


async def copies(conn: AsyncConnection, case_id: uuid.UUID) -> list[Copy]:
    cur = await conn.execute(
        _COPY + " where p.case_id = %s and p.duplicate_of_page_id is not null order by p.bundle_page_no", (case_id,)
    )
    return [Copy(*r) for r in await cur.fetchall()]


async def copy_review(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> Copy | None:
    """The page's review state; a page never set aside has status `kept` or none (see `review_copy`)."""
    cur = await conn.execute(
        "select p.id, p.bundle_page_no, o.id, o.bundle_page_no, a.duplicate_status, coalesce(a.duplicate_version, 0)"
        " from app.pages p left join app.pages o on o.id = p.duplicate_of_page_id"
        " left join app.page_analysis a on a.page_id = p.id where p.id = %s and p.case_id = %s",
        (page_id, case_id),
    )
    row = await cur.fetchone()
    return Copy(row[0], row[1], row[2], row[3], row[4] or "", row[5]) if row else None


async def review_copy(
    conn: AsyncConnection, page_id: uuid.UUID, status: str, person_id: uuid.UUID, version: int
) -> int | None:
    """Record the review if `version` is still current; the new version, or None if it moved on."""
    cur = await conn.execute(
        "update app.page_analysis set duplicate_status = %s, duplicate_by = %s, duplicate_at = now(),"
        " duplicate_version = duplicate_version + 1, updated_at = now()"
        " where page_id = %s and duplicate_version = %s returning duplicate_version",
        (status, person_id, page_id, version),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    if status == "kept":
        await conn.execute("update app.pages set duplicate_of_page_id = null where id = %s", (page_id,))
    return int(row[0])


async def set_aside_as_copy(
    conn: AsyncConnection, page_id: uuid.UUID, original: uuid.UUID, status: str, person_id: uuid.UUID
) -> int | None:
    """Point the page at the page it copies and record who decided; the new version, None if it has no notes row."""
    cur = await conn.execute(
        "update app.page_analysis set duplicate_status = %s, duplicate_by = %s, duplicate_at = now(),"
        " duplicate_version = coalesce(duplicate_version, 0) + 1, updated_at = now()"
        " where page_id = %s returning duplicate_version",
        (status, person_id, page_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    await conn.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (original, page_id))
    return int(row[0])


async def page_id_of(conn: AsyncConnection, case_id: uuid.UUID, page_no: int) -> uuid.UUID | None:
    cur = await conn.execute(
        "select id from app.pages where case_id = %s and bundle_page_no = %s and image_path is not null"
        " and not quarantined",
        (case_id, page_no),
    )
    row = await cur.fetchone()
    return row[0] if row else None


# Record gaps and missing documents (increment 6) --------------------------------------------------


async def gap_pages(conn: AsyncConnection, case_id: uuid.UUID) -> list[gaps.Page]:
    """Every page not set aside as a copy, with its printed dates and its facts' kinds and dates."""
    cur = await conn.execute(
        "select p.id, p.bundle_page_no, p.source_type, coalesce(a.printed_dates, '{}'),"
        " coalesce(array_agg(f.kind) filter (where f.id is not null), '{}'),"
        " coalesce(array_agg(f.event_date) filter (where f.id is not null), '{}')"
        " from app.pages p left join app.page_analysis a on a.page_id = p.id"
        " left join app.facts f on f.page_id = p.id and f.status <> 'rejected'"
        " where p.case_id = %s and p.duplicate_of_page_id is null and not p.quarantined"
        " group by p.id, p.bundle_page_no, p.source_type, a.printed_dates order by p.bundle_page_no",
        (case_id,),
    )
    return [
        gaps.Page(
            page_id=r[0],
            page_no=r[1],
            source_type=r[2],
            printed=tuple(r[3]),
            facts=tuple(zip(r[4], r[5], strict=True)),
        )
        for r in await cur.fetchall()
    ]


async def store_gaps(conn: AsyncConnection, case_id: uuid.UUID, found: list[gaps.Gap]) -> None:
    """Keep the case's unreviewed gaps equal to `found`; a gap the expert reviewed is left as it is."""
    for gap in found:
        await conn.execute(
            "insert into app.record_gaps (case_id, source_type, fact_kind, gap_start, gap_end, last_page_id)"
            " values (%s, %s, %s, %s, %s, %s) on conflict (case_id, source_type, fact_kind) do update set"
            " gap_start = excluded.gap_start, gap_end = excluded.gap_end, last_page_id = excluded.last_page_id,"
            " updated_at = now() where app.record_gaps.confirmed is null and (app.record_gaps.gap_start,"
            " app.record_gaps.gap_end, app.record_gaps.last_page_id) is distinct from"
            " (excluded.gap_start, excluded.gap_end, excluded.last_page_id)",
            (case_id, gap.source_type, gap.fact_kind, gap.gap_start, gap.gap_end, gap.last_page_id),
        )
    await conn.execute(
        "delete from app.record_gaps where case_id = %s and confirmed is null"
        " and not (source_type || chr(31) || fact_kind = any(%s::text[]))",
        (case_id, [f"{g.source_type}\x1f{g.fact_kind}" for g in found]),
    )


async def pages_to_match(conn: AsyncConnection, case_id: uuid.UUID) -> list[tuple[uuid.UUID, int]]:
    """Pages read by the model whose references are not matched yet, copies left out."""
    cur = await conn.execute(
        "select p.id, p.bundle_page_no from app.pages p join app.page_analysis a on a.page_id = p.id"
        " where p.case_id = %s and a.extracted_at is not null and a.references_checked_at is null"
        " and p.duplicate_of_page_id is null and not p.quarantined order by p.bundle_page_no",
        (case_id,),
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def sealed_extraction(conn: AsyncConnection, page_id: uuid.UUID) -> bytes | None:
    cur = await conn.execute("select extraction from app.page_analysis where page_id = %s", (page_id,))
    row = await cur.fetchone()
    return bytes(row[0]) if row and row[0] is not None else None


async def page_numbers(conn: AsyncConnection, case_id: uuid.UUID) -> set[int]:
    cur = await conn.execute("select bundle_page_no from app.pages where case_id = %s", (case_id,))
    return {int(r[0]) for r in await cur.fetchall()}


@dataclass(frozen=True)
class NewMissing:
    id: uuid.UUID
    reference_key: str
    word_ids: list[str]
    sealed: bytes
    reference_date: dt.date | None
    confidence: float | None


async def add_missing(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, items: list[NewMissing]) -> None:
    """Add the page's missing items (once each) and mark its references matched."""
    for item in items:
        await conn.execute(
            "insert into app.missing_items (id, case_id, citing_page_id, reference_key, word_ids, description,"
            " reference_date, confidence) values (%s, %s, %s, %s, %s, %s, %s, %s)"
            " on conflict (citing_page_id, reference_key) do nothing",
            (
                item.id,
                case_id,
                page_id,
                item.reference_key,
                item.word_ids,
                item.sealed,
                item.reference_date,
                item.confidence,
            ),
        )
    await conn.execute(
        "update app.page_analysis set references_checked_at = now(), updated_at = now() where page_id = %s",
        (page_id,),
    )


@dataclass(frozen=True)
class MissingItem:
    id: uuid.UUID
    citing_page_id: uuid.UUID
    citing_page_no: int
    word_ids: list[str]
    description: bytes
    reference_date: dt.date | None
    status: str
    reason: bytes | None
    received_page_id: uuid.UUID | None
    received_page_no: int | None
    version: int
    created_at: dt.datetime


_MISSING = (
    "select m.id, m.citing_page_id, c.bundle_page_no, m.word_ids, m.description, m.reference_date, m.status,"
    " m.reason, m.received_page_id, r.bundle_page_no, m.version, m.created_at"
    " from app.missing_items m join app.pages c on c.id = m.citing_page_id"
    " left join app.pages r on r.id = m.received_page_id"
)


def _item(r: Any) -> MissingItem:
    return MissingItem(
        r[0],
        r[1],
        r[2],
        list(r[3]),
        bytes(r[4]),
        r[5],
        r[6],
        bytes(r[7]) if r[7] is not None else None,
        r[8],
        r[9],
        r[10],
        r[11],
    )


async def missing_items(conn: AsyncConnection, case_id: uuid.UUID) -> list[MissingItem]:
    cur = await conn.execute(_MISSING + " where m.case_id = %s order by c.bundle_page_no, m.reference_key", (case_id,))
    return [_item(r) for r in await cur.fetchall()]


async def missing_item(conn: AsyncConnection, case_id: uuid.UUID, item_id: uuid.UUID) -> MissingItem | None:
    cur = await conn.execute(_MISSING + " where m.case_id = %s and m.id = %s", (case_id, item_id))
    row = await cur.fetchone()
    return _item(row) if row else None


async def page_added_after(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, after: dt.datetime) -> bool:
    cur = await conn.execute(
        "select 1 from app.pages where id = %s and case_id = %s and created_at > %s", (page_id, case_id, after)
    )
    return await cur.fetchone() is not None


async def change_missing(
    conn: AsyncConnection,
    item_id: uuid.UUID,
    *,
    status: str,
    reason: bytes | None,
    received_page_id: uuid.UUID | None,
    person_id: uuid.UUID,
    version: int,
) -> int | None:
    """Record the change if `version` is still current; the new version, or None if it moved on."""
    cur = await conn.execute(
        "update app.missing_items set status = %s, reason = %s, received_page_id = %s, updated_by = %s,"
        " version = version + 1, updated_at = now() where id = %s and version = %s returning version",
        (status, reason, received_page_id, person_id, item_id, version),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else None


# --- bundle ready ----------------------------------------------------------------------------------


async def split_in_job(conn: AsyncConnection, case_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """Whether a document of the case was cut into pages since this job was queued."""
    cur = await conn.execute(
        "select exists (select 1 from app.document_scans s join app.jobs j on j.id = %s"
        " where s.case_id = %s and s.split_at >= j.created_at)",
        (job_id, case_id),
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def activate_case(conn: AsyncConnection, case_id: uuid.UUID) -> None:
    await conn.execute("update app.cases set status = 'active' where id = %s and status = 'processing'", (case_id,))


async def case_people(conn: AsyncConnection, case_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """The case's named expert and its organisation."""
    cur = await conn.execute("select named_expert_id, organisation_id from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    assert row is not None
    return row[0], row[1]


# --- embeddings ----------------------------------------------------------------------------------


async def pages_to_embed(conn: AsyncConnection, case_id: uuid.UUID, model: str) -> list[uuid.UUID]:
    """Pages with no embedding from the current model (none yet, or one from an older model)."""
    cur = await conn.execute(
        "select p.id from app.pages p left join app.page_embeddings e on e.page_id = p.id"
        " where p.case_id = %s and not p.quarantined and (e.page_id is null or e.model <> %s)"
        " order by p.bundle_page_no",
        (case_id, model),
    )
    return [r[0] for r in await cur.fetchall()]


async def store_embedding(
    conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, model: str, vector: tuple[float, ...]
) -> None:
    literal = "[" + ",".join(repr(float(v)) for v in vector) + "]"
    await conn.execute(
        "insert into app.page_embeddings (page_id, case_id, model, embedding)"
        " values (%s, %s, %s, %s::extensions.vector)"
        " on conflict (page_id) do update set model = excluded.model, embedding = excluded.embedding,"
        " updated_at = now()",
        (page_id, case_id, model, literal),
    )


# --- sorted view -----------------------------------------------------------------------------------


async def sorted_pages(conn: AsyncConnection, case_id: uuid.UUID) -> list[tuple[uuid.UUID, int, str | None, Any]]:
    """Each page kept in the reading set: id, number, source and the earliest date printed on it.
    Page-level data only, so an unread page reveals nothing from a suggestion (rule 3). The caller
    orders by the pack's sources."""
    cur = await conn.execute(
        "select p.id, p.bundle_page_no, p.source_type,"
        " (select min(d) from app.page_analysis a, unnest(a.printed_dates) d where a.page_id = p.id)"
        " from app.pages p where p.case_id = %s and not p.quarantined",
        (case_id,),
    )
    return [(r[0], int(r[1]), r[2], r[3]) for r in await cur.fetchall()]


# --- bundle summary --------------------------------------------------------------------------------


async def source_counts(conn: AsyncConnection, case_id: uuid.UUID) -> list[tuple[str | None, int]]:
    cur = await conn.execute(
        "select source_type, count(*) from app.pages where case_id = %s and not quarantined"
        " group by source_type order by count(*) desc, source_type nulls last",
        (case_id,),
    )
    return [(r[0], int(r[1])) for r in await cur.fetchall()]


async def summary_counts(conn: AsyncConnection, case_id: uuid.UUID) -> tuple[int, int, int, int]:
    """Pages set aside as copies, unreadable pages, gaps not dismissed and open missing items."""
    cur = await conn.execute(
        "select"
        " (select count(*) from app.pages p join app.page_analysis a on a.page_id = p.id"
        "   where p.case_id = %(c)s and p.duplicate_of_page_id is not null and not p.quarantined"
        "   and a.duplicate_status in ('suggested', 'confirmed')),"
        " (select count(*) from app.pages where case_id = %(c)s and unreadable and not quarantined),"
        " (select count(*) from app.record_gaps where case_id = %(c)s and confirmed is distinct from false),"
        " (select count(*) from app.missing_items where case_id = %(c)s and status = 'open')",
        {"c": case_id},
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])


@dataclass(frozen=True)
class DocumentRow:
    id: uuid.UUID
    kind: str
    filename: str
    pages: int | None
    first_page_no: int | None
    last_page_no: int | None
    received_on: Any
    obtained_how: str | None
    bundle_version: int
    created_at: Any
    media_type: str | None = None
    part_of: uuid.UUID | None = None
    unreadable_reason: str | None = None


_DOCUMENT = (
    "select d.id, d.kind, d.filename, d.pages, min(p.bundle_page_no), max(p.bundle_page_no), d.received_on,"
    " d.obtained_how, d.bundle_version, d.created_at, s.media_type, dp.parent_document_id,"
    " d.unreadable_reason"
    " from app.documents d left join app.pages p on p.document_id = d.id"
    " left join app.document_scans s on s.document_id = d.id"
    " left join app.document_parts dp on dp.document_id = d.id"
)
_GROUP = " group by d.id, s.media_type, dp.parent_document_id"


async def documents(conn: AsyncConnection, case_id: uuid.UUID) -> list[DocumentRow]:
    cur = await conn.execute(
        _DOCUMENT + " where d.case_id = %s" + _GROUP + " order by min(p.bundle_page_no) nulls last, d.created_at",
        (case_id,),
    )
    return [DocumentRow(*r) for r in await cur.fetchall()]


async def document(conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID) -> DocumentRow | None:
    cur = await conn.execute(
        _DOCUMENT + " where d.case_id = %s and d.id = %s" + _GROUP,
        (case_id, document_id),
    )
    row = await cur.fetchone()
    return DocumentRow(*row) if row else None


async def document_storage_paths(conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID) -> tuple[str | None, list[uuid.UUID]]:
    """The document's storage_path and the page ids whose storage objects must be removed."""
    cur = await conn.execute("select storage_path from app.documents where id = %s and case_id = %s", (document_id, case_id))
    row = await cur.fetchone()
    storage_path = row[0] if row else None
    cur = await conn.execute("select id from app.pages where document_id = %s and case_id = %s", (document_id, case_id))
    page_ids = [r[0] for r in await cur.fetchall()]
    return storage_path, page_ids


async def delete_document(conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    cur = await conn.execute("delete from app.documents where id = %s and case_id = %s returning id", (document_id, case_id))
    return (await cur.fetchone()) is not None


async def change_provenance(conn: AsyncConnection, document_id: uuid.UUID, changes: dict[str, Any]) -> None:
    allowed = {"received_on", "obtained_how"}
    cols = [c for c in changes if c in allowed]
    if not cols:
        return
    sets = ", ".join(f"{c} = %({c})s" for c in cols)
    await conn.execute(f"update app.documents set {sets} where id = %(id)s", {**changes, "id": document_id})  # noqa: S608


def versions_of(rows: list[DocumentRow]) -> list[dict[str, Any]]:
    """What each delivery of records added, from the case's documents."""
    found: dict[int, dict[str, Any]] = {}
    for d in rows:
        v = found.setdefault(
            d.bundle_version,
            {
                "bundle_version": d.bundle_version,
                "documents": 0,
                "pages": 0,
                "first_page_no": None,
                "last_page_no": None,
                "added_at": d.created_at,
            },
        )
        if d.part_of is None:
            v["documents"] += 1
        v["pages"] += d.pages or 0
        if d.first_page_no is not None:
            v["first_page_no"] = min(x for x in (v["first_page_no"], d.first_page_no) if x is not None)
            v["last_page_no"] = max(x for x in (v["last_page_no"], d.last_page_no) if x is not None)
        v["added_at"] = min(v["added_at"], d.created_at)
    return [found[k] for k in sorted(found)]


# --- later records ---------------------------------------------------------------------------------


async def join_next_version(conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """The version after the newest one already cut into pages."""
    await conn.execute(
        "update app.documents set bundle_version = (select coalesce(max(d.bundle_version), 0) + 1"
        " from app.documents d where d.case_id = %(c)s and d.id <> %(d)s"
        " and exists (select 1 from app.pages p where p.document_id = d.id))"
        " where id = %(d)s and case_id = %(c)s",
        {"c": case_id, "d": document_id},
    )


async def appendable(conn: AsyncConnection, case_id: uuid.UUID, document_ids: list[uuid.UUID]) -> tuple[int, int]:
    """How many of the documents are the case's, and how many of those are already cut into pages."""
    cur = await conn.execute(
        "select count(*), count(*) filter (where exists (select 1 from app.pages p where p.document_id = d.id))"
        " from app.documents d where d.case_id = %s and d.id = any(%s)",
        (case_id, document_ids),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


async def group_version(conn: AsyncConnection, case_id: uuid.UUID, document_ids: list[uuid.UUID]) -> int:
    """Put the documents in one new version, after every other document's; returns it."""
    cur = await conn.execute(
        "update app.documents set bundle_version = (select coalesce(max(bundle_version), 0) + 1"
        " from app.documents where case_id = %(c)s and not (id = any(%(ids)s)))"
        " where case_id = %(c)s and id = any(%(ids)s) returning bundle_version",
        {"c": case_id, "ids": document_ids},
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


# --- instructing-party requests --------------------------------------------------------------------


async def open_missing(conn: AsyncConnection, case_id: uuid.UUID) -> list[MissingItem]:
    """Open missing items, in page order."""
    return [i for i in await missing_items(conn, case_id) if i.status == "open"]


async def clearer_copy_pages(conn: AsyncConnection, case_id: uuid.UUID) -> list[tuple[uuid.UUID, int]]:
    """Unreadable pages, and pages the expert asked a clearer copy of, still in the bundle (not set aside),
    as (id, page number)."""
    cur = await conn.execute(
        "select id, bundle_page_no from app.pages"
        " where case_id = %s and (unreadable or needs_clearer_copy) and not quarantined"
        " order by bundle_page_no",
        (case_id,),
    )
    return [(r[0], int(r[1])) for r in await cur.fetchall()]


async def request_wording_facts(conn: AsyncConnection, case_id: uuid.UUID) -> tuple[str, str]:
    """The case's label and the named expert's name ('' if not set)."""
    cur = await conn.execute(
        "select c.label, coalesce(p.full_name, '') from app.cases c left join app.people p on p.id = c.named_expert_id"
        " where c.id = %s",
        (case_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0], row[1]


async def case_due_date(conn: AsyncConnection, case_id: uuid.UUID) -> dt.date | None:
    cur = await conn.execute("select due_date from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    return row[0] if row else None


async def count_known(conn: AsyncConnection, table: str, case_id: uuid.UUID, ids: list[uuid.UUID]) -> int:
    assert table in {"missing_items", "pages"}
    cur = await conn.execute(f"select count(*) from app.{table} where case_id = %s and id = any(%s)", (case_id, ids))
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def log_request(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    *,
    request_id: uuid.UUID,
    missing_item_ids: list[uuid.UUID],
    page_ids: list[uuid.UUID],
    subject: bytes,
    body: bytes,
    follow_up_on: dt.date | None,
    person_id: uuid.UUID,
    quarantined_page_ids: list[uuid.UUID] | None = None,
) -> None:
    await conn.execute(
        "insert into app.instructing_party_requests (id, case_id, missing_item_ids, page_ids, subject, body_text,"
        " sent_via, follow_up_on, created_by, quarantined_page_ids)"
        " values (%s, %s, %s, %s, %s, %s, 'mailto', %s, %s, %s)",
        (
            request_id,
            case_id,
            missing_item_ids,
            page_ids,
            subject,
            body,
            follow_up_on,
            person_id,
            quarantined_page_ids or [],
        ),
    )


async def mark_requested(
    conn: AsyncConnection, case_id: uuid.UUID, ids: list[uuid.UUID], person_id: uuid.UUID
) -> list[uuid.UUID]:
    """Open items among `ids` become `requested`; returns the ones that moved."""
    cur = await conn.execute(
        "update app.missing_items set status = 'requested', updated_by = %s, version = version + 1,"
        " updated_at = now() where case_id = %s and id = any(%s) and status = 'open' returning id",
        (person_id, case_id, ids),
    )
    return [r[0] for r in await cur.fetchall()]


BUNDLE_JOBS = ("process_bundle", "process_pages", "reprocess_case")


async def latest_processing(
    conn: AsyncConnection, case_id: uuid.UUID, kinds: tuple[str, ...]
) -> dict[str, object] | None:
    """The case's newest job of these kinds: its progress only, never page text."""
    cur = await conn.execute(
        "select id, kind, status, step, pct, eta_seconds, step_done, step_total, error_code from app.jobs"
        " where case_id = %s and kind = any(%s) order by created_at desc, id desc limit 1",
        (case_id, list(kinds)),
    )
    row = await cur.fetchone()
    names = ("id", "kind", "status", "step", "pct", "eta_seconds", "step_done", "step_total", "error_code")
    return None if row is None else dict(zip(names, row, strict=True))


async def request_cancel(conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    """Asks the bundle jobs queued or running now to stop at the next page boundary. False when nothing
    is processing or a stop is already asked for every such job."""
    cur = await conn.execute(
        "with live as (select max(created_at) as since from app.jobs"
        "   where case_id = %(case)s and kind = any(%(kinds)s) and status in ('queued', 'running'))"
        " insert into app.case_processing (case_id, cancel_requested_at, cancel_requested_by)"
        " select %(case)s, now(), %(person)s from live where live.since is not null"
        " on conflict (case_id) do update set cancel_requested_at = now(),"
        "   cancel_requested_by = excluded.cancel_requested_by, updated_at = now()"
        " where app.case_processing.cancel_requested_at is null or app.case_processing.cancel_requested_at <"
        "   (select max(created_at) from app.jobs where case_id = %(case)s and kind = any(%(kinds)s)"
        "    and status in ('queued', 'running'))"
        " returning case_id",
        {"case": case_id, "person": person_id, "kinds": list(BUNDLE_JOBS)},
    )
    return await cur.fetchone() is not None


async def failed_job_ids(
    conn: AsyncConnection, case_id: uuid.UUID, kinds: tuple[str, ...]
) -> list[tuple[uuid.UUID, str]]:
    """The case's failed jobs of these kinds, oldest first: id and kind only."""
    cur = await conn.execute(
        "select id, kind from app.jobs where case_id = %s and kind = any(%s) and status = 'failed'"
        " order by created_at, id",
        (case_id, list(kinds)),
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def requeue_failed(
    conn: AsyncConnection, job_id: uuid.UUID, queue: str, priority: int, changes_case: bool
) -> bool:
    """failed -> queued through the database function; False when the job was no longer failed."""
    cur = await conn.execute("select app.requeue_failed_job(%s, %s, %s, %s)", (job_id, queue, priority, changes_case))
    row = await cur.fetchone()
    return bool(row and row[0])


async def cancel_requested(conn: AsyncConnection, job_id: uuid.UUID) -> bool:
    """Whether a stop was asked for after this job was queued."""
    cur = await conn.execute(
        "select exists (select 1 from app.jobs j join app.case_processing c on c.case_id = j.case_id"
        " where j.id = %s and c.cancel_requested_at > j.created_at)",
        (job_id,),
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def opt_in_reprocess(conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> None:
    await conn.execute(
        "insert into app.case_processing (case_id, reprocess_opted_at, reprocess_opted_by) values (%s, now(), %s)"
        " on conflict (case_id) do update set reprocess_opted_at = now(),"
        " reprocess_opted_by = excluded.reprocess_opted_by, updated_at = now()",
        (case_id, person_id),
    )


async def reset_for_reprocess(conn: AsyncConnection, case_id: uuid.UUID) -> int:
    """Pages extracted before the expert opted in to reprocessing are extracted again: their unverified
    suggestions are superseded (rejected); verified and edited facts are left as they are."""
    cur = await conn.execute(
        "select a.page_id from app.page_analysis a join app.case_processing c on c.case_id = a.case_id"
        " where a.case_id = %s and a.extracted_at < c.reprocess_opted_at",
        (case_id,),
    )
    stale = [r[0] for r in await cur.fetchall()]
    if stale:
        await conn.execute(
            "update app.facts set status = 'rejected' where page_id = any(%s) and status = 'suggested'", (stale,)
        )
        await conn.execute("update app.page_analysis set extracted_at = null where page_id = any(%s)", (stale,))
    return len(stale)


# Pages that are not this person's record (increment 11b) ------------------------------------------


async def pages_for_identity(conn: AsyncConnection, case_id: uuid.UUID) -> list[uuid.UUID]:
    """Pages with words, not copies and not quarantined: the pages whose identifiers are compared."""
    cur = await conn.execute(
        "select p.id from app.pages p where p.case_id = %s and p.text_source is not null"
        " and p.duplicate_of_page_id is null and not p.quarantined order by p.bundle_page_no",
        (case_id,),
    )
    return [r[0] for r in await cur.fetchall()]


async def add_suggestions(conn: AsyncConnection, case_id: uuid.UUID, page_ids: list[uuid.UUID], reason: str) -> None:
    """The pipeline's suggestions; a page anyone has suggested, decided or dismissed before is left alone."""
    await conn.execute(
        "insert into app.page_quarantines (case_id, page_id, reason)"
        " select %s, p, %s from unnest(%s::uuid[]) as p"
        " where not exists (select 1 from app.page_quarantines q where q.page_id = p)",
        (case_id, reason, page_ids),
    )


@dataclass(frozen=True)
class Quarantine:
    id: uuid.UUID
    page_id: uuid.UUID
    page_no: int
    status: str
    reason: str


_QUARANTINE = (
    "select q.id, q.page_id, p.bundle_page_no, q.status, q.reason from app.page_quarantines q"
    " join app.pages p on p.id = q.page_id"
)


async def quarantines(conn: AsyncConnection, case_id: uuid.UUID) -> list[Quarantine]:
    """The case's open suggestions and confirmed quarantines (deleted or not), in page order."""
    cur = await conn.execute(
        _QUARANTINE + " where q.case_id = %s and q.status in ('suggested', 'confirmed', 'purged')"
        " order by p.bundle_page_no",
        (case_id,),
    )
    return [Quarantine(*r) for r in await cur.fetchall()]


async def live_quarantine(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> Quarantine | None:
    cur = await conn.execute(
        _QUARANTINE + " where q.case_id = %s and q.page_id = %s and q.status in ('suggested', 'confirmed')",
        (case_id, page_id),
    )
    row = await cur.fetchone()
    return Quarantine(*row) if row else None


async def page_no_of(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> int | None:
    cur = await conn.execute("select bundle_page_no from app.pages where id = %s and case_id = %s", (page_id, case_id))
    row = await cur.fetchone()
    return int(row[0]) if row else None


async def suggest_quarantine(
    conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, reason: str, person_id: uuid.UUID
) -> Quarantine:
    await conn.execute(
        "insert into app.page_quarantines (case_id, page_id, reason, suggested_by) values (%s, %s, %s, %s)",
        (case_id, page_id, reason, person_id),
    )
    found = await live_quarantine(conn, case_id, page_id)
    assert found is not None
    return found


async def confirm_quarantine(conn: AsyncConnection, q: Quarantine, reason: str, person_id: uuid.UUID) -> None:
    """The page is quarantined and every fact on it rejected; their earlier statuses are kept for undo."""
    cur = await conn.execute(
        "select id, status from app.facts where page_id = %s and status <> 'rejected'", (q.page_id,)
    )
    prior = [{"id": str(r[0]), "status": r[1]} for r in await cur.fetchall()]
    if prior:
        await conn.execute(
            "update app.facts set status = 'rejected' where id = any(%s)", ([uuid.UUID(f["id"]) for f in prior],)
        )
    await conn.execute(
        "update app.page_quarantines set status = 'confirmed', reason = %s, confirmed_by = %s, confirmed_at = now(),"
        " prior_facts = %s where id = %s",
        (reason, person_id, Jsonb(prior), q.id),
    )
    await conn.execute(
        "update app.pages set quarantined = true, quarantine_reason = %s, quarantined_by = %s,"
        " quarantined_at = now() where id = %s",
        (reason, person_id, q.page_id),
    )


async def close_quarantine(conn: AsyncConnection, q: Quarantine, person_id: uuid.UUID) -> str:
    """Dismisses a suggestion, or undoes a quarantine: the page comes back and the facts that were
    verified or edited before it are verified again. Returns the new status."""
    if q.status == "suggested":
        await conn.execute(
            "update app.page_quarantines set status = 'dismissed', closed_by = %s, closed_at = now() where id = %s",
            (person_id, q.id),
        )
        return "dismissed"
    cur = await conn.execute(
        "update app.page_quarantines set status = 'undone', closed_by = %s, closed_at = now() where id = %s"
        " returning prior_facts",
        (person_id, q.id),
    )
    row = await cur.fetchone()
    kept = [uuid.UUID(f["id"]) for f in (row[0] if row else []) if f["status"] in ("verified", "edited")]
    if kept:
        await conn.execute(
            "update app.facts set status = 'verified' where id = any(%s) and status = 'rejected'", (kept,)
        )
    await conn.execute(
        "update app.pages set quarantined = false, quarantine_reason = null, quarantined_by = null,"
        " quarantined_at = null where id = %s",
        (q.page_id,),
    )
    return "undone"


async def reset_undone(conn: AsyncConnection, case_id: uuid.UUID) -> None:
    """A page brought back from quarantine is extracted again."""
    await conn.execute(
        "update app.page_analysis a set extracted_at = null from app.page_quarantines q"
        " where q.page_id = a.page_id and a.case_id = %s and q.status = 'undone' and q.closed_at > a.extracted_at",
        (case_id,),
    )


async def count_quarantined(conn: AsyncConnection, case_id: uuid.UUID, page_ids: list[uuid.UUID]) -> int:
    """How many of `page_ids` are the case's confirmed (or already deleted) quarantines."""
    cur = await conn.execute(
        "select count(distinct page_id) from app.page_quarantines"
        " where case_id = %s and page_id = any(%s) and status in ('confirmed', 'purged')",
        (case_id, page_ids),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def was_purged(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> bool:
    cur = await conn.execute(
        "select 1 from app.page_quarantines where case_id = %s and page_id = %s and status = 'purged'",
        (case_id, page_id),
    )
    return await cur.fetchone() is not None


@dataclass(frozen=True)
class PurgeCandidate:
    quarantine_id: uuid.UUID
    case_id: uuid.UUID
    page_id: uuid.UUID
    codes: dict[str, str]
    confirmed_on: dt.date
    told: bool


async def quarantines_to_purge(conn: AsyncConnection, run_date: dt.date) -> list[PurgeCandidate]:
    """Every confirmed quarantine, across cases (the worker's daily schedule only)."""
    cur = await conn.execute("select * from app.quarantines_to_purge(%s)", (run_date,))
    return [
        PurgeCandidate(r[0], r[1], r[2], {"jurisdiction": r[3], "case_type": r[4], "specialty": r[5]}, r[6], bool(r[7]))
        for r in await cur.fetchall()
    ]


async def purge_quarantined_page(conn: AsyncConnection, quarantine_id: uuid.UUID) -> str | None:
    """Deletes the page's text; returns where the uploaded file was kept once no page of it remains."""
    cur = await conn.execute("select app.purge_quarantined_page(%s)", (quarantine_id,))
    row = await cur.fetchone()
    return None if row is None or row[0] is None else str(row[0])


async def document_in_case(conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    cur = await conn.execute("select 1 from app.documents where id = %s and case_id = %s", (document_id, case_id))
    return await cur.fetchone() is not None


async def replacement_taken(conn: AsyncConnection, page_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    """The page is already superseded or being replaced, or the copy already replaces a page."""
    cur = await conn.execute(
        "select 1 from app.page_replacements where page_id = %s or document_id = %s"
        " union all select 1 from app.pages where id = %s and superseded_by_page_id is not null",
        (page_id, document_id, page_id),
    )
    return await cur.fetchone() is not None


async def ask_replacement(
    conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, document_id: uuid.UUID, person_id: uuid.UUID
) -> None:
    await conn.execute(
        "insert into app.page_replacements (case_id, page_id, document_id, requested_by) values (%s, %s, %s, %s)",
        (case_id, page_id, document_id, person_id),
    )


async def link_replacements(conn: AsyncConnection, case_id: uuid.UUID) -> dict[uuid.UUID, tuple[uuid.UUID, int]]:
    """Each replacement whose copy has been cut into pages: the old page is superseded by the copy's
    first page. Returns old page -> (new page, its number) for the ones linked now."""
    cur = await conn.execute(
        "with first as ("
        "  select distinct on (p.document_id) p.document_id, p.id, p.bundle_page_no from app.pages p"
        "  join app.page_replacements r on r.document_id = p.document_id"
        "  where r.case_id = %s and r.new_page_id is null order by p.document_id, p.bundle_page_no)"
        " update app.page_replacements r set new_page_id = f.id, linked_at = now() from first f"
        " where r.document_id = f.document_id and r.new_page_id is null"
        " returning r.page_id, f.id, f.bundle_page_no",
        (case_id,),
    )
    linked = {r[0]: (r[1], int(r[2])) for r in await cur.fetchall()}
    for old, (new, _) in linked.items():
        await conn.execute(
            "update app.pages set superseded_by_page_id = %s where id = %s and superseded_by_page_id is null",
            (new, old),
        )
    return linked


async def split_blocked(conn: AsyncConnection, page_id: uuid.UUID) -> bool:
    """The page is set aside (a clearer copy replaces it) or is already split."""
    cur = await conn.execute(
        "select 1 from app.pages where id = %s and superseded_by_page_id is not null"
        " union all select 1 from app.page_replacements where page_id = %s"
        " union all select 1 from app.documents where split_of_page_id = %s",
        (page_id, page_id, page_id),
    )
    return await cur.fetchone() is not None


async def add_split_document(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    *,
    document_id: uuid.UUID,
    filename: str,
    sha256: str,
    storage_path: str,
) -> int:
    """The halves of a page, as a generated document of their own; returns its bundle version."""
    await conn.execute(
        "insert into app.documents (id, case_id, kind, filename, storage_path, sha256, split_of_page_id, uploaded_by)"
        " values (%s, %s, %s, %s, %s, %s, %s, app.current_person_id())",
        (document_id, case_id, RECORDS_KIND, filename, storage_path, sha256, page_id),
    )
    return await group_version(conn, case_id, [document_id])


async def link_splits(conn: AsyncConnection, case_id: uuid.UUID) -> int:
    """Pages of a split document point back at the page they came from, and the original's unchecked
    suggestions are rejected (the halves read the same words). Checked facts are never touched."""
    cur = await conn.execute(
        "update app.pages p set split_of_page_id = d.split_of_page_id from app.documents d"
        " where p.document_id = d.id and d.case_id = %s and d.split_of_page_id is not null"
        " and p.split_of_page_id is null returning d.split_of_page_id",
        (case_id,),
    )
    originals = list({r[0] for r in await cur.fetchall()})
    if originals:
        await conn.execute(
            "update app.facts set status = 'rejected' where page_id = any(%s) and status = 'suggested'", (originals,)
        )
    return len(originals)


async def split_count(conn: AsyncConnection, case_id: uuid.UUID) -> int:
    cur = await conn.execute(
        "select count(*) from app.documents where case_id = %s and split_of_page_id is not null", (case_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


@dataclass(frozen=True)
class UndatedFact:
    fact_id: uuid.UUID
    page_id: uuid.UUID
    kind: str
    fields: dict[str, Any]
    word_ids: list[str]
    status: str


async def undated_facts(conn: AsyncConnection, case_id: uuid.UUID) -> list[UndatedFact]:
    cur = await conn.execute(
        "select id, page_id, kind, fields, word_ids, status from app.facts"
        " where case_id = %s and event_date is null and status <> 'rejected' order by page_id, id",
        (case_id,),
    )
    return [
        UndatedFact(
            fact_id=uuid.UUID(str(r[0])),
            page_id=uuid.UUID(str(r[1])),
            kind=str(r[2]),
            fields=dict(r[3] or {}),
            word_ids=list(r[4] or []),
            status=str(r[5]),
        )
        for r in await cur.fetchall()
    ]


async def set_fact_event_date(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    fact_id: uuid.UUID,
    when: dt.date,
    basis: str,
    word_ids: Sequence[str] | None = None,
) -> bool:
    """Dates a fact that has none; `word_ids`, when given, replace what it cites (now with the date's words)."""
    cur = await conn.execute(
        "update app.facts set event_date = %s, date_basis = %s, word_ids = coalesce(%s::text[], word_ids)"
        " where case_id = %s and id = %s and event_date is null returning id",
        (when, basis, list(word_ids) if word_ids is not None else None, case_id, fact_id),
    )
    return (await cur.fetchone()) is not None
