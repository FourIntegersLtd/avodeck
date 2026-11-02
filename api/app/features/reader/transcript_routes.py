"""Slice B routes: reading by transcript (ADR 0028). Added to the reader router.

Entries sit outside the blind-read gate because they carry only the printed words, never a fact (rule 3);
the server alone decides, from what the reader reports seeing, when a page counts as read.
The Word copy is built by a job (`export_transcript`) and handed over once, to the person who asked.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.record_entries import (
    entries_progress,
    entries_ready,
    entries_status,
    pipeline_running,
    request_entries,
)
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import CurrentScope, DbConn
from app.core.iterkit import iter_chunks
from app.core.jobs import registry
from app.core.packs import PackLibrary
from app.core.storage import CaseFileStorage
from app.features.reader import transcript, transcript_jobs
from app.features.reader.router import router
from app.features.reader.schemas import (
    JobOut,
    OutlineCoverage,
    OutlineSection,
    RetryOut,
    SeenIn,
    SeenOut,
    TranscriptEntryOut,
    TranscriptFileIn,
    TranscriptList,
    TranscriptOutline,
)

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Read = Annotated[CaseAccess, Depends(require_case_access("page.read"))]
Export = Annotated[CaseAccess, Depends(require_case_access("case.export"))]

CHUNK = 1 << 16
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF = "application/pdf"


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
        status = await entries_status(conn, case_id)
        can_prepare = "page.read" in access.allowed_actions  # others do not start the work
        # A failed job is not queued again by a poll; the reader asks again (`/transcript/retry`).
        if status == "idle" and can_prepare:
            await request_entries(conn, case_id)  # queues once, and not while pages await the pipeline
            status = await entries_status(conn, case_id)
        progress = await entries_progress(conn, case_id)
        return TranscriptList(
            status=status,
            total=0,
            entries=[],
            prepared_pages=progress.entries_done,
            total_pages=progress.total,
            extracted_pages=progress.extracted,
            waiting_for="pipeline" if progress.extracted < progress.total else "entries",
            pipeline_running=await pipeline_running(conn, case_id),
            can_prepare=can_prepare,
        )
    crypto: CaseCrypto = request.app.state.crypto
    library: PackLibrary = request.app.state.packs
    got = await transcript.entries(conn, crypto, library, case_id, offset=offset, limit=limit)
    return TranscriptList(
        status="ready",
        total=got.total,
        picture_pages=got.picture_pages,
        unread_pages=got.unread_pages,
        entries=[
            TranscriptEntryOut(**{f: getattr(e, f) for f in TranscriptEntryOut.model_fields}) for e in got.entries
        ],
    )


@router.get("/transcript/outline", response_model=TranscriptOutline)
async def transcript_outline(case_id: uuid.UUID, request: Request, access: View, conn: DbConn) -> TranscriptOutline:
    """The sections (with where each starts in the paged list) and the page coverage; counts and labels only."""
    if not await entries_ready(conn, case_id):
        return TranscriptOutline(status=await entries_status(conn, case_id))  # asking never queues work
    library: PackLibrary = request.app.state.packs
    got = await transcript.outline(conn, library, case_id)
    return TranscriptOutline(
        status="ready",
        sections=[OutlineSection(key=k, label=lb, count=c, offset=o) for k, lb, c, o in got.sections],
        coverage=OutlineCoverage(
            total_pages=got.total_pages,
            with_entries=got.with_entries,
            no_entries=got.no_entries,
            unreadable=got.unreadable,
            set_aside=got.set_aside,
        ),
    )


@router.post("/transcript/retry", status_code=202, response_model=RetryOut)
async def transcript_retry(case_id: uuid.UUID, access: Read, conn: DbConn) -> RetryOut:
    """The reader asks again after the entries job failed; queues once, nothing while one is waiting."""
    await request_entries(conn, case_id)
    return RetryOut(status=await entries_status(conn, case_id))


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
    except transcript.NotReady:
        raise ApiError("not_ready", "The record is still being prepared.", 409) from None
    return SeenOut(pages_read=pages)


@router.post("/transcript/download", status_code=202, response_model=JobOut)
async def transcript_download_request(
    case_id: uuid.UUID, access: Export, view: View, conn: DbConn, body: TranscriptFileIn | None = None
) -> JobOut:
    cur = await conn.execute("select named_expert_id from app.cases where id = %s", (case_id,))
    named = (await cur.fetchone() or (None,))[0]
    if named is None:
        raise ApiError("no_named_expert", "This case has no named expert yet.", 409)
    pdf = body is not None and body.format == "pdf"
    job = await registry.enqueue(conn, case_id, transcript_jobs.KIND_PDF if pdf else transcript_jobs.KIND)
    await audit.record(conn, action="case.transcript_requested", target=f"export:{job}", case_id=case_id)
    return JobOut(job_id=job)


@router.get(
    "/transcript/download/{job_id}", response_class=StreamingResponse, responses={200: {"content": {DOCX: {}, PDF: {}}}}
)
async def transcript_download(
    case_id: uuid.UUID, job_id: uuid.UUID, request: Request, access: Export, conn: DbConn, after: BackgroundTasks
) -> StreamingResponse:
    cur = await conn.execute(
        "select status, requested_by is not distinct from app.current_person_id(), kind from app.jobs"
        " where id = %s and case_id = %s and kind = any(%s)",
        (job_id, case_id, [transcript_jobs.KIND, transcript_jobs.KIND_PDF]),
    )
    job = await cur.fetchone()
    if job is None or not job[1]:
        raise _not_found()
    if job[0] != "done":
        raise ApiError("conflict", "The file is not ready yet.", 409)
    storage: CaseFileStorage | None = request.app.state.storage
    crypto: CaseCrypto | None = request.app.state.crypto
    if storage is None or crypto is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    fmt = "pdf" if job[2] == transcript_jobs.KIND_PDF else "docx"
    data = await transcript_jobs.take(conn, storage, crypto, case_id, job_id, fmt)
    after.add_task(transcript_jobs.remove, storage, case_id, job_id, fmt)  # runs once the audit row is committed
    return StreamingResponse(
        iter_chunks(data, CHUNK),
        media_type=PDF if fmt == "pdf" else DOCX,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="transcript-{job_id}.{fmt}"',
        },
    )
