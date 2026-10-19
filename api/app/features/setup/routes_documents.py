"""Screen 04: the files dropped on a case and how many pages each has, before anything is processed.

Pages are counted from the PDF's page tree (no OCR); `page_count` is null for a file that is not a readable
PDF or is very large. When the bundle pipeline has already numbered a document's pages, its count is used.
The count is made on first ask and remembered by file checksum.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.core.crypto import CaseCrypto
from app.core.http import DbConn
from app.core.storage import SupabaseStorage
from app.features.setup import pdf
from app.features.setup.router import router
from app.features.setup.routes_case import ViewContent

COUNT_UP_TO = 150 * 1024 * 1024


class CaseFile(BaseModel):
    document_id: uuid.UUID
    filename: str
    kind: str
    size: int | None = Field(description="Bytes, as uploaded.")
    page_count: int | None = Field(description="Pages in the PDF; null when unknown.")


async def _count(request: Request, conn: DbConn, case_id: uuid.UUID, prefix: str, sha256: str) -> int | None:
    known, count = pdf.remembered(sha256)
    if known:
        return count
    storage: SupabaseStorage | None = request.app.state.storage
    crypto: CaseCrypto | None = request.app.state.crypto
    if storage is None or crypto is None:
        return None
    parts = []
    for path in sorted(await storage.list(prefix)):
        parts.append(await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path))
    count = await asyncio.to_thread(pdf.page_count, b"".join(parts))
    pdf.remember(sha256, count)
    return count


@router.get("/cases/{case_id}/files", response_model=list[CaseFile])
async def list_files(case_id: uuid.UUID, access: ViewContent, request: Request, conn: DbConn) -> list[CaseFile]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select d.id, d.filename, d.kind, d.sha256, d.pages, d.storage_path, u.size"
            " from app.documents d left join app.uploads u on u.document_id = d.id"
            " where d.case_id = %s order by d.created_at, d.id",
            (case_id,),
        )
        rows = await cur.fetchall()
    files: list[CaseFile] = []
    for r in rows:
        pages: int | None = r["pages"]
        if pages is None and r["storage_path"] and (r["size"] is None or r["size"] <= COUNT_UP_TO):
            pages = await _count(request, conn, case_id, r["storage_path"], r["sha256"])
        files.append(
            CaseFile(document_id=r["id"], filename=r["filename"], kind=r["kind"], size=r["size"], page_count=pages)
        )
    return files
