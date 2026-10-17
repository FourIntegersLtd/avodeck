"""Close the case (screen 26): architecture 5.2 "26 Close the case" and 5.3 "Close (slice H)".

Every route checks case access (rule 9). Export, retention and holds need step-up (rule 10): the
access dependency enforces it for those actions. `delete_now` also needs `case.delete_now`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi.responses import StreamingResponse

from app.core.auth.access import CaseAccess, require_case_access
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import DbConn
from app.core.http import router as make_router
from app.core.packs import PackLibrary
from app.core.storage import SupabaseStorage
from app.features.close import jobs as _jobs  # noqa: F401  (registers export_case and delete_case)
from app.features.close import repo, service
from app.features.close.schemas import (
    AuditEntry,
    AuditTrail,
    Certificate,
    CloseSummary,
    Hold,
    HoldCreate,
    JobQueued,
    RetentionChange,
)

router = make_router(prefix="/v1/cases/{case_id}", tags=["close"])


def _packs(request: Request) -> PackLibrary:
    library: PackLibrary = request.app.state.packs
    return library


def _storage(request: Request) -> SupabaseStorage:
    storage: SupabaseStorage | None = request.app.state.storage
    if storage is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    return storage


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = request.app.state.crypto
    if crypto is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    return crypto


Packs = Annotated[PackLibrary, Depends(_packs)]


def _access(action: str) -> type[CaseAccess]:
    return Annotated[CaseAccess, Depends(require_case_access(action))]  # type: ignore[return-value]


@router.get("/close", response_model=CloseSummary)
async def close_summary(
    case_id: uuid.UUID, conn: DbConn, packs: Packs, _: _access("case.view_metadata")  # type: ignore[valid-type]
) -> CloseSummary:
    return await service.summary(conn, packs, case_id)


@router.post("/settle", response_model=CloseSummary)
async def settle(
    case_id: uuid.UUID, conn: DbConn, packs: Packs, _: _access("case.settle")  # type: ignore[valid-type]
) -> CloseSummary:
    await service.settle(conn, packs, case_id)
    return await service.summary(conn, packs, case_id)


@router.post("/archive", response_model=CloseSummary)
async def archive(
    case_id: uuid.UUID, conn: DbConn, packs: Packs, _: _access("case.archive")  # type: ignore[valid-type]
) -> CloseSummary:
    await service.archive(conn, packs, case_id)
    return await service.summary(conn, packs, case_id)


@router.post("/reopen", response_model=CloseSummary)
async def reopen(
    case_id: uuid.UUID, conn: DbConn, packs: Packs, _: _access("case.reopen")  # type: ignore[valid-type]
) -> CloseSummary:
    await service.reopen(conn, case_id)
    return await service.summary(conn, packs, case_id)


@router.put("/retention", response_model=CloseSummary)
async def change_retention(
    case_id: uuid.UUID,
    body: RetentionChange,
    conn: DbConn,
    packs: Packs,
    _: _access("case.retention"),  # type: ignore[valid-type]
) -> CloseSummary:
    job_id = await service.change_retention(conn, packs, case_id, body.policy)
    return await service.summary(conn, packs, case_id, job_id=job_id)


@router.post("/legal-holds", status_code=201, response_model=Hold)
async def set_legal_hold(
    case_id: uuid.UUID, body: HoldCreate, conn: DbConn, _: _access("legal_hold.set")  # type: ignore[valid-type]
) -> Hold:
    return await service.set_hold(conn, case_id, body.reason, body.until)


@router.post("/legal-holds/{hold_id}/release", response_model=Hold)
async def release_legal_hold(
    case_id: uuid.UUID, hold_id: uuid.UUID, conn: DbConn, _: _access("legal_hold.release")  # type: ignore[valid-type]
) -> Hold:
    return await service.release_hold(conn, case_id, hold_id)


@router.get("/audit", response_model=AuditTrail)
async def audit_trail(
    case_id: uuid.UUID, conn: DbConn, _: _access("case.view_audit")  # type: ignore[valid-type]
) -> AuditTrail:
    return AuditTrail(entries=[AuditEntry(**row) for row in await repo.audit_trail(conn, case_id)])


@router.get("/deletion-certificate", response_model=Certificate)
async def deletion_certificate(
    case_id: uuid.UUID, conn: DbConn, _: _access("case.view_certificate")  # type: ignore[valid-type]
) -> Certificate:
    row = await repo.deletion(conn, case_id)
    if row is None or row["deleted_at"] is None:
        raise ApiError("not_found", "Not found.", 404)
    return Certificate(**row)


@router.post("/export", status_code=202, response_model=JobQueued)
async def request_export(
    case_id: uuid.UUID, conn: DbConn, _: _access("case.export")  # type: ignore[valid-type]
) -> JobQueued:
    return JobQueued(job_id=await service.request_export(conn, case_id))


@router.get(
    "/export/{job_id}/download",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}, "description": "The export, once."}},
)
async def download_export(
    job_id: uuid.UUID,
    request: Request,
    conn: DbConn,
    access: _access("case.export"),  # type: ignore[valid-type]
) -> Response:
    # Storage is looked up after the access check, so a refusal never says whether storage is ready.
    await service.check_export_done(conn, access.case_id, job_id)
    data = await service.download_export(conn, _storage(request), _crypto(request), access, job_id)
    return StreamingResponse(
        service.chunks(data),
        media_type="application/zip",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="export-{job_id}.zip"',
        },
    )
