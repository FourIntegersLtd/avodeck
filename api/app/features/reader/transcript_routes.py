"""Slice B routes: reading by transcript (ADR 0028). Added to the reader router.

Entries sit outside the blind-read gate because they carry only the printed words, never a fact (rule 3);
the server alone decides, from what the reader reports seeing, when a page counts as read.
The Word copy is built by a job (`export_transcript`) and handed over once, to the person who asked.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.record_entries import entries_ready, entries_status, request_entries
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import CurrentScope, DbConn
from app.core.jobs import registry
from app.core.packs import PackLibrary
from app.core.storage import SupabaseStorage
from app.features.reader import transcript, transcript_jobs
from app.features.reader.router import router
from app.features.reader.schemas import JobOut, SeenIn, SeenOut, TranscriptEntryOut, TranscriptList

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Read = Annotated[CaseAccess, Depends(require_case_access("page.read"))]
Export = Annotated[CaseAccess, Depends(require_case_access("case.export"))]

CHUNK = 1 << 16
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


@router.get("/transcript/entries", response_model=TranscriptList)
async def transcript_entries(
    case_id: uuid.UUID,
    request: Request,
    access: View,
    conn: DbConn,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TranscriptList:
    if not await entries_ready(conn, case_id):
        await request_entries(conn, case_id)  # queues once; nothing while one is waiting
        return TranscriptList(status=await entries_status(conn, case_id), total=0, entries=[])
    crypto: CaseCrypto = request.app.state.crypto
    library: PackLibrary = request.app.state.packs
    got = await transcript.entries(conn, crypto, library, case_id, offset=offset, limit=limit)
    return TranscriptList(
        status="ready",
        total=got.total,
        entries=[
            TranscriptEntryOut(**{f: getattr(e, f) for f in TranscriptEntryOut.model_fields}) for e in got.entries
        ],
    )


@router.post("/transcript/seen", response_model=SeenOut)
async def transcript_seen(case_id: uuid.UUID, body: SeenIn, access: Read, conn: DbConn, scope: CurrentScope) -> SeenOut:
    try:
        pages = await transcript.record_seen(
            conn,
            case_id,
            scope.person_id,
            entry_id=body.entry_id,
            page_no=body.page_no,
            dwell_ms=body.dwell_ms,
            picture_opened=body.picture_opened,
        )
    except transcript.UnknownEntry:
        raise _not_found() from None
    return SeenOut(pages_read=pages)


@router.post("/transcript/download", status_code=202, response_model=JobOut)
async def transcript_download_request(case_id: uuid.UUID, access: Export, conn: DbConn) -> JobOut:
    job = await registry.enqueue(conn, case_id, transcript_jobs.KIND)
    await audit.record(conn, action="case.transcript_requested", target=f"export:{job}", case_id=case_id)
    return JobOut(job_id=job)


def _chunks(data: bytes) -> Iterator[bytes]:
    view = memoryview(data)
    for start in range(0, len(view), CHUNK):
        yield bytes(view[start : start + CHUNK])


@router.get("/transcript/download/{job_id}", response_class=StreamingResponse, responses={200: {"content": {DOCX: {}}}})
async def transcript_download(
    case_id: uuid.UUID, job_id: uuid.UUID, request: Request, access: Export, conn: DbConn
) -> StreamingResponse:
    cur = await conn.execute(
        "select status, requested_by is not distinct from app.current_person_id() from app.jobs"
        " where id = %s and case_id = %s and kind = %s",
        (job_id, case_id, transcript_jobs.KIND),
    )
    job = await cur.fetchone()
    if job is None or not job[1]:
        raise _not_found()
    if job[0] != "done":
        raise ApiError("conflict", "The file is not ready yet.", 409)
    storage: SupabaseStorage | None = request.app.state.storage
    crypto: CaseCrypto | None = request.app.state.crypto
    if storage is None or crypto is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    data = await transcript_jobs.take(conn, storage, crypto, case_id, job_id)
    await audit.record(conn, action="case.transcript_downloaded", target=f"export:{job_id}", case_id=case_id)
    return StreamingResponse(
        _chunks(data),
        media_type=DOCX,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="transcript-{job_id}.docx"',
        },
    )
