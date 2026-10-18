"""Slice B routes: reading pages (S09). Every route checks case access first (rule 9).

The page itself never carries facts; `/facts` sits behind the blind-read gate (rule 3).
Responses carrying the page picture are never cached.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import Depends, Query, Request, Response

from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.reading import require_page_read
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import CurrentScope, DbConn
from app.core.http import router as make_router
from app.core.storage import SupabaseStorage
from app.features.reader import repo
from app.features.reader.schemas import FactList, FactOut, PageDetail, PageList, PageSummary, Progress, ReadIn, ReadOut

router = make_router(prefix="/v1/cases/{case_id}", tags=["reader"])

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]


def _not_found() -> ApiError:
    return ApiError("not_found", "We couldn't find that page.", 404)


async def _page(conn: DbConn, case_id: uuid.UUID, page_no: int, scope: CurrentScope) -> dict:  # type: ignore[type-arg]
    found = await repo.page(conn, case_id, page_no, scope.person_id)
    if found is None:
        raise _not_found()
    return found


@router.get("/pages", response_model=PageList)
async def list_pages(
    case_id: uuid.UUID,
    access: View,
    conn: DbConn,
    scope: CurrentScope,
    filter: Literal["unread", "period", "source"] | None = None,
    source: str | None = None,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> PageList:
    rows = await repo.list_pages(
        conn, case_id, scope.person_id, filter=filter, source=source, cursor=cursor, limit=limit + 1
    )
    more = len(rows) > limit
    pages = [PageSummary(page_no=r["bundle_page_no"], page_id=r["id"], **_summary(r)) for r in rows[:limit]]
    return PageList(pages=pages, next_cursor=pages[-1].page_no if more else None)


@router.get("/pages/{page_no}", response_model=PageDetail)
async def get_page(case_id: uuid.UUID, page_no: int, access: View, conn: DbConn, scope: CurrentScope) -> PageDetail:
    r = await _page(conn, case_id, page_no, scope)
    return PageDetail(
        page_no=r["bundle_page_no"],
        page_id=r["id"],
        **_summary(r),
        rotation=r["rotation"],
        relevance=r["relevance"],
        needs_clearer_copy=r["needs_clearer_copy"],
        superseded_by_page_no=r["superseded_by_page_no"],
        handwriting=r["handwriting"],
        has_transcription=r["has_transcription"],
        first_read_at=r["first_read_at"],
    )


@router.get("/pages/{page_no}/image", response_class=Response)
async def page_image(
    case_id: uuid.UUID, page_no: int, access: View, request: Request, conn: DbConn, scope: CurrentScope
) -> Response:
    storage: SupabaseStorage | None = getattr(request.app.state, "storage", None)
    crypto: CaseCrypto | None = getattr(request.app.state, "crypto", None)
    if storage is None or crypto is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    r = await _page(conn, case_id, page_no, scope)
    if r["image_path"] is None:
        raise _not_found()
    path = r["image_path"]
    picture = await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path)
    return Response(picture, media_type="image/jpeg", headers={"cache-control": "private, no-store"})


@router.post("/pages/{page_no}/read", response_model=ReadOut)
async def mark_read(
    case_id: uuid.UUID,
    page_no: int,
    body: ReadIn,
    access: Annotated[CaseAccess, Depends(require_case_access("page.read"))],
    conn: DbConn,
    scope: CurrentScope,
) -> ReadOut:
    r = await _page(conn, case_id, page_no, scope)
    await repo.record_read(conn, case_id, r["id"], scope.person_id, body.dwell_seconds)
    after = await _page(conn, case_id, page_no, scope)
    return ReadOut(page_no=page_no, read=True, first_read_at=after["first_read_at"])


@router.get("/pages/{page_no}/facts", response_model=FactList)
async def page_facts(
    case_id: uuid.UUID,
    page_no: int,
    access: Annotated[CaseAccess, Depends(require_page_read())],
    conn: DbConn,
    scope: CurrentScope,
) -> FactList:
    r = await _page(conn, case_id, page_no, scope)
    if r["duplicate_of_page_id"] is not None:  # a copy's facts show on the original
        return FactList(facts=[])
    return FactList(facts=[FactOut(**f) for f in await repo.page_facts(conn, case_id, r["id"])])


@router.get("/progress", response_model=Progress)
async def progress(case_id: uuid.UUID, access: View, conn: DbConn, scope: CurrentScope) -> Progress:
    read, total, seconds_left = await repo.progress(conn, case_id, scope.person_id)
    return Progress(read=read, total=total, seconds_left=seconds_left)


def _summary(r: dict) -> dict:  # type: ignore[type-arg]
    return {k: r[k] for k in ("source_type", "read", "in_period", "unreadable", "duplicate_of_page_no")}
