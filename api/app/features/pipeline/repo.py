"""SQL for slice A's tables (and the page rows the pipeline creates)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from psycopg import AsyncConnection

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
