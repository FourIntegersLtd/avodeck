"""Small helpers shared by the slice F routes: access, the case's packs, encryption and storage."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel

from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.reading import PageReadLog
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import DbConn
from app.core.packs import MergedPack, PackLibrary
from app.core.storage import SupabaseStorage

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Build = Annotated[CaseAccess, Depends(require_case_access("report.build"))]
Write = Annotated[CaseAccess, Depends(require_case_access("opinion.write"))]
Sign = Annotated[CaseAccess, Depends(require_case_access("report.sign"))]
Serve = Annotated[CaseAccess, Depends(require_case_access("report.serve"))]
Withdraw = Annotated[CaseAccess, Depends(require_case_access("report.withdraw"))]


def not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


def invalid(*fields: str) -> ApiError:
    return ApiError("invalid", "Check the highlighted fields.", 422, fields=list(fields))


class Queued(BaseModel):
    job_id: uuid.UUID


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = request.app.state.crypto
    if crypto is None:
        raise ApiError("unavailable", "Saving is not ready.", 503)
    return crypto


def _storage(request: Request) -> SupabaseStorage:
    storage: SupabaseStorage | None = request.app.state.storage
    if storage is None:
        raise ApiError("unavailable", "Files are not ready.", 503)
    return storage


def _reads(request: Request) -> PageReadLog | None:
    reads: PageReadLog | None = getattr(request.app.state, "page_reads", None)
    return reads


Crypto = Annotated[CaseCrypto, Depends(_crypto)]
Storage = Annotated[SupabaseStorage, Depends(_storage)]
Reads = Annotated[PageReadLog | None, Depends(_reads)]


async def pack_for(conn: Any, library: PackLibrary, case_id: uuid.UUID) -> MergedPack:
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    return library.combine(str(row[0]), str(row[1]), str(row[2]))


async def case_pack(request: Request, conn: DbConn, case_id: uuid.UUID) -> MergedPack:
    return await pack_for(conn, request.app.state.packs, case_id)


Pack = Annotated[MergedPack, Depends(case_pack)]
