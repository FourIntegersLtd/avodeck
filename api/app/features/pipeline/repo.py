"""SQL for slice A's tables (and the page rows the pipeline creates)."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

RECORDS_KIND = "records"  # the core document kind that becomes numbered pages


@dataclass(frozen=True)
class Document:
    id: uuid.UUID
    storage_path: str


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
        "select d.id, d.storage_path from app.documents d join app.document_scans s on s.document_id = d.id"
        " where d.case_id = %s and s.clean and s.split_at is null and d.kind = %s"
        " order by d.created_at, d.id",
        (case_id, RECORDS_KIND),
    )
    return [Document(r[0], r[1]) for r in await cur.fetchall()]


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


@dataclass(frozen=True)
class PageRow:
    id: uuid.UUID
    document_id: uuid.UUID
    bundle_page_no: int
    first_page_no: int


async def pages_without_image(conn: AsyncConnection, case_id: uuid.UUID) -> list[PageRow]:
    cur = await conn.execute(
        "select p.id, p.document_id, p.bundle_page_no,"
        " (select min(q.bundle_page_no) from app.pages q where q.document_id = p.document_id)"
        " from app.pages p where p.case_id = %s and p.image_path is null order by p.bundle_page_no",
        (case_id,),
    )
    return [PageRow(r[0], r[1], r[2], r[3]) for r in await cur.fetchall()]


async def document_path(conn: AsyncConnection, document_id: uuid.UUID) -> str:
    cur = await conn.execute("select storage_path from app.documents where id = %s", (document_id,))
    row = await cur.fetchone()
    assert row is not None and row[0]
    return str(row[0])


async def set_image(conn: AsyncConnection, page_id: uuid.UUID, image_path: str) -> None:
    await conn.execute("update app.pages set image_path = %s where id = %s", (image_path, page_id))


async def pages_without_words(conn: AsyncConnection, case_id: uuid.UUID) -> list[uuid.UUID]:
    cur = await conn.execute(
        "select id from app.pages where case_id = %s and text_source is null and image_path is not null"
        " order by bundle_page_no",
        (case_id,),
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
        " and a.extracted_at is null order by p.bundle_page_no",
        (case_id,),
    )
    return [r[0] for r in await cur.fetchall()]


@dataclass(frozen=True)
class StoredWord:
    word_id: str
    sealed: bytes
    polygon: list[float]
    struck: bool


async def words_of(conn: AsyncConnection, page_id: uuid.UUID) -> list[StoredWord]:
    cur = await conn.execute(
        "select word_id, text, polygon, struck from app.ocr_words where page_id = %s order by n", (page_id,)
    )
    return [StoredWord(r[0], bytes(r[1]), list(r[2]), bool(r[3])) for r in await cur.fetchall()]


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
        " printed_dates, extracted_at) values (%s, %s, 'extract', %s, %s, %s, %s, %s, now())"
        " on conflict (page_id) do update set stage = excluded.stage, extraction = excluded.extraction,"
        " needs_human = excluded.needs_human, reason = excluded.reason, model_id = excluded.model_id,"
        " printed_dates = excluded.printed_dates, extracted_at = excluded.extracted_at, updated_at = now()",
        (page_id, case_id, sealed, needs_human, reason, model_id, printed_dates or []),
    )
