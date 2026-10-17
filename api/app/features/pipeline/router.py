"""Slice A's routes (architecture 5.2): bundle summary, duplicates, thumbnails, missing items.

- `GET /v1/cases/{id}/duplicates`: each page set aside as a copy, the page it copies and its review.
- `PATCH /v1/cases/{id}/pages/{page_id}/duplicate` (If-Match): `confirm` keeps it set aside; `undo`
  restores the page with everything found on it, and it is never set aside again (F13).
- `GET /v1/cases/{id}/pages/{page_no}/thumbnail`: the page's small picture, never cached.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request, Response

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import CurrentScope, DbConn, IfMatch, etag, router, stale
from app.core.storage import SupabaseStorage
from app.features.pipeline import paths, repo
from app.features.pipeline.schemas import DuplicatePage, DuplicateReview, Duplicates

pipeline_router = router(prefix="/v1/cases", tags=["pipeline"])

_REVIEW = {"confirm": ("confirmed", "page.duplicate_confirmed"), "undo": ("kept", "page.duplicate_undone")}


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


def _page(copy: repo.Copy) -> DuplicatePage:
    return DuplicatePage(
        page_id=copy.page_id,
        page_no=copy.page_no,
        copy_of_page_id=copy.copy_of_page_id,
        copy_of_page_no=copy.copy_of_page_no,
        status=copy.status,  # type: ignore[arg-type]
        version=copy.version,
    )


@pipeline_router.get("/{case_id}/duplicates", response_model=Duplicates)
async def list_duplicates(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.view_content"))],
    conn: DbConn,
) -> Duplicates:
    return Duplicates(duplicates=[_page(c) for c in await repo.copies(conn, case_id)])


@pipeline_router.patch("/{case_id}/pages/{page_id}/duplicate", response_model=DuplicatePage)
async def review_duplicate(
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("page.rotate"))],
    version: IfMatch,
    body: DuplicateReview,
    conn: DbConn,
    scope: CurrentScope,
    response: Response,
) -> DuplicatePage:
    current = await repo.copy_review(conn, case_id, page_id)
    if current is None:
        raise _not_found()
    if current.version != version:
        raise stale()
    if current.copy_of_page_id is None or current.status not in ("suggested", "confirmed"):
        raise ApiError("conflict", "This page is not set aside as a copy.", 409)
    status, action = _REVIEW[body.action]
    moved = await repo.review_copy(conn, page_id, status, scope.person_id, version)
    if moved is None:
        raise stale()
    await audit.record(conn, action=action, target=f"page:{page_id}", case_id=case_id)
    after = await repo.copy_review(conn, case_id, page_id)
    assert after is not None
    response.headers["etag"] = etag(moved)
    return _page(after)


@pipeline_router.get("/{case_id}/pages/{page_no}/thumbnail", response_class=Response)
async def page_thumbnail(
    case_id: uuid.UUID,
    page_no: int,
    access: Annotated[CaseAccess, Depends(require_case_access("case.view_content"))],
    request: Request,
    conn: DbConn,
) -> Response:
    storage: SupabaseStorage | None = getattr(request.app.state, "storage", None)
    crypto: CaseCrypto | None = getattr(request.app.state, "crypto", None)
    if storage is None or crypto is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    page_id = await repo.page_id_of(conn, case_id, page_no)
    if page_id is None:
        raise _not_found()
    path = paths.thumbnail_path(case_id, page_id)
    picture = await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path)
    return Response(picture, media_type="image/jpeg", headers={"cache-control": "private, no-store"})
