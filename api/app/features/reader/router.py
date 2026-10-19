"""Slice B routes: reading pages (S09). Every route checks case access first (rule 9).

The page itself never carries facts; `/facts` sits behind the blind-read gate (rule 3).
Responses carrying the page picture are never cached.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import Depends, Query, Request, Response

from app.core.ai import provenance
from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.fact_flags import POINTS, RECHECK
from app.core.contracts.pipeline import EntryPointMissing, extract_page_text, process_pages
from app.core.contracts.reading import blind_read, require_page_read
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import CurrentScope, DbConn, IfMatch, etag, stale
from app.core.http import router as make_router
from app.core.packs import PackLibrary
from app.core.storage import SupabaseStorage
from app.features.reader import fields as fact_fields
from app.features.reader import repo, today  # noqa: F401 (registers the Today providers)
from app.features.reader.schemas import (
    FactList,
    FactOut,
    FlagOut,
    JobOut,
    MoveIn,
    PageChange,
    PageDetail,
    PageList,
    PageSummary,
    PageWord,
    PageWords,
    Progress,
    ReadIn,
    ReadOut,
    RotateIn,
    TranscriptionIn,
    UnreadablePage,
    UnreadableQueue,
    VerifyIn,
)

router = make_router(prefix="/v1/cases/{case_id}", tags=["reader"])

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Verify = Annotated[CaseAccess, Depends(require_case_access("fact.verify"))]
Transcribe = Annotated[CaseAccess, Depends(require_case_access("page.transcribe"))]

# (from, action) -> to, as docs/spec/state-machines.md allows
_CHECKS = {
    ("suggested", "correct"): "verified",
    ("rejected", "correct"): "verified",
    ("suggested", "edit"): "edited",
    ("suggested", "not_right"): "rejected",
    ("verified", "not_right"): "rejected",
    ("edited", "not_right"): "rejected",
}
_CHECKED = ("verified", "edited")


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


@router.get("/pages/unreadable", response_model=UnreadableQueue)
async def unreadable_pages(case_id: uuid.UUID, access: View, conn: DbConn) -> UnreadableQueue:
    pages = [
        UnreadablePage(
            **r,
            done=r["has_transcription"]
            or r["needs_clearer_copy"]
            or r["relevance"] == "nothing"
            or r["is_copy"]
            or r["has_clearer_copy"],
        )
        for r in await repo.unreadable_pages(conn, case_id)
    ]
    return UnreadableQueue(pages=pages, done=sum(p.done for p in pages), total=len(pages))


@router.get("/pages/{page_no}", response_model=PageDetail)
async def get_page(case_id: uuid.UUID, page_no: int, access: View, conn: DbConn, scope: CurrentScope) -> PageDetail:
    return _detail(await _page(conn, case_id, page_no, scope))


def _detail(r: dict) -> PageDetail:  # type: ignore[type-arg]
    return PageDetail(
        page_no=r["bundle_page_no"],
        page_id=r["id"],
        **_summary(r),
        period_start=r["period_start"],
        period_end=r["period_end"],
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


@router.get("/pages/{page_no}/words", response_model=PageWords)
async def page_words(
    case_id: uuid.UUID,
    page_no: int,
    access: Annotated[CaseAccess, Depends(require_page_read())],
    request: Request,
    response: Response,
    conn: DbConn,
    scope: CurrentScope,
) -> PageWords:
    """The page's words and their places, for choosing the words a changed fact cites (behind rule 3)."""
    r = await _page(conn, case_id, page_no, scope)
    response.headers["cache-control"] = "private, no-store"
    words = await repo.page_word_boxes(conn, _crypto(request), case_id, r["id"])
    return PageWords(words=[PageWord(**w) for w in words])


@router.put("/pages/{page_no}/transcription", response_model=JobOut, status_code=202)
async def save_transcription(
    case_id: uuid.UUID,
    page_no: int,
    body: TranscriptionIn,
    access: Transcribe,
    conn: DbConn,
    scope: CurrentScope,
    request: Request,
) -> JobOut:
    """The expert's own typing of the page: it counts as reading it, and the page is read again from it."""
    r = await _page(conn, case_id, page_no, scope)
    await repo.save_transcription(conn, _crypto(request), case_id, r["id"], body.text, body.relevance)
    await repo.record_read(conn, case_id, r["id"], scope.person_id, 0)
    try:
        job_id = await extract_page_text(conn, case_id, r["id"])
    except EntryPointMissing:
        raise _not_ready() from None
    return JobOut(job_id=job_id)


@router.patch("/pages/{page_no}", response_model=PageDetail)
async def change_page(
    case_id: uuid.UUID, page_no: int, body: PageChange, access: Transcribe, conn: DbConn, scope: CurrentScope
) -> PageDetail:
    r = await _page(conn, case_id, page_no, scope)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("needs_clearer_copy", False) is None:
        raise ApiError("invalid", "Say whether a clearer copy is needed.", 422)
    await repo.change_page(conn, r["id"], changes)
    return _detail(await _page(conn, case_id, page_no, scope))


@router.post("/pages/{page_id}/rotate", response_model=JobOut, status_code=202)
async def rotate_page(
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    body: RotateIn,
    access: Annotated[CaseAccess, Depends(require_case_access("page.rotate"))],
    conn: DbConn,
) -> JobOut:
    """Turns the page and reads it again; its number stays (slice A re-maps or flags its checked facts)."""
    if await repo.rotate(conn, case_id, page_id, body.degrees) is None:
        raise _not_found()
    try:
        job_id = await process_pages(conn, case_id, [page_id])
    except EntryPointMissing:
        raise _not_ready() from None
    return JobOut(job_id=job_id)


def _not_ready() -> ApiError:
    return ApiError("unavailable", "Reading the page again isn't ready yet.", 503)


@router.get("/progress", response_model=Progress)
async def progress(case_id: uuid.UUID, access: View, conn: DbConn, scope: CurrentScope) -> Progress:
    read, total, seconds_left = await repo.progress(conn, case_id, scope.person_id)
    return Progress(read=read, total=total, seconds_left=seconds_left)


def _fact_not_found() -> ApiError:
    return ApiError("not_found", "We couldn't find that fact.", 404)


def _invalid() -> ApiError:
    return ApiError("invalid", "The words you chose do not show that.", 422)


async def _check_fields(request: Request, conn: DbConn, case_id: uuid.UUID, kind: str, sent: dict) -> None:  # type: ignore[type-arg]
    """422 unless every sent detail is one the case pack gives the fact's kind, with a value of its type."""
    codes = await repo.case_codes(conn, case_id)
    if codes is None:
        raise _fact_not_found()
    library: PackLibrary = request.app.state.packs
    known = fact_fields.kinds(library.for_case(codes).fact_kinds())
    if not fact_fields.fits(known.get(kind), sent):
        raise ApiError("invalid_fields", "Those details don't fit this kind of fact.", 422)


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = getattr(request.app.state, "crypto", None)
    if crypto is None:
        raise ApiError("unavailable", "The case key service is not ready.", 503)
    return crypto


async def _checkable(conn: DbConn, case_id: uuid.UUID, fact_id: uuid.UUID, version: int, scope: CurrentScope) -> dict:  # type: ignore[type-arg]
    """The fact, once the expert has read its page (rule 3) and holds its current version."""
    found = await repo.fact(conn, case_id, fact_id)
    if found is None:
        raise _fact_not_found()
    if not await repo.has_read(conn, case_id, found["page_no"], scope.person_id):
        raise blind_read()
    if found["version"] != version:
        raise stale()
    return found


def _changed(response: Response, after: dict | None) -> FactOut:  # type: ignore[type-arg]
    if after is None:
        raise stale()
    response.headers["etag"] = etag(after["version"])
    return FactOut(**after)


@router.post("/facts/{fact_id}/verify", response_model=FactOut)
async def verify_fact(
    case_id: uuid.UUID,
    fact_id: uuid.UUID,
    body: VerifyIn,
    access: Verify,
    version: IfMatch,
    request: Request,
    response: Response,
    conn: DbConn,
    scope: CurrentScope,
) -> FactOut:
    before = await _checkable(conn, case_id, fact_id, version, scope)
    status = _CHECKS.get((before["status"], body.action))
    rechecking = before["needs_recheck"] and before["status"] in _CHECKED
    if status is None and rechecking and body.action in ("correct", "edit"):
        # "Please check" after a re-read: confirm as it was, or change it to cite the words there now.
        status = before["status"] if body.action == "correct" else "edited"
    if status is None:
        raise ApiError("conflict", "This suggestion cannot be changed that way.", 409)
    value, word_ids, bbox = before["value"], before["word_ids"], before["bbox"]
    fields, event_date = before["fields"], before["event_date"]
    if rechecking and body.action == "correct":
        # Rule 2: the page's words were replaced, so the old word ids must still show the value.
        words = await repo.page_words(conn, _crypto(request), case_id, before["page_id"])
        if not provenance.supported(value, word_ids, words):
            raise _invalid()
    if rechecking and body.action == "edit" and not body.word_ids:
        raise ApiError("words_needed", "Choose the words on the page that show this.", 422)
    if body.action == "edit":
        if not body.new_value:
            raise _invalid()
        value, word_ids = body.new_value, body.word_ids or before["word_ids"]
        words = await repo.page_words(conn, _crypto(request), case_id, before["page_id"])
        if not provenance.supported(value, word_ids, words):
            raise _invalid()
        if body.word_ids:
            bbox = await repo.word_box(conn, before["page_id"], word_ids)
        if body.fields is not None:
            await _check_fields(request, conn, case_id, before["kind"], body.fields)
            fields = body.fields
        if "event_date" in body.model_fields_set:
            event_date = body.event_date
    after = await repo.change_fact(
        conn,
        fact_id,
        version,
        status=status,
        value=value,
        word_ids=word_ids,
        page_id=before["page_id"],
        bbox=bbox,
        fields=fields,
        event_date=event_date,
    )
    out = _changed(response, after)
    assert after is not None
    if before["needs_recheck"]:  # after the change: facts_guard reads the flag for verified -> edited
        await repo.set_flag(conn, case_id, fact_id, RECHECK, scope.person_id, on=False)
        out = out.model_copy(update={"needs_recheck": False})
    await repo.log_check(
        conn,
        case_id,
        scope.person_id,
        body.action,
        before,
        after,
        reason=body.reason if body.action == "not_right" else None,
    )
    return out


@router.post("/facts/{fact_id}/move", response_model=FactOut)
async def move_fact(
    case_id: uuid.UUID,
    fact_id: uuid.UUID,
    body: MoveIn,
    access: Verify,
    version: IfMatch,
    request: Request,
    response: Response,
    conn: DbConn,
    scope: CurrentScope,
) -> FactOut:
    """Move a fact to the clearer copy of its page, citing that page's words. History is kept."""
    before = await _checkable(conn, case_id, fact_id, version, scope)
    target = await repo.page_id_by_no(conn, case_id, body.to_page_no)
    if target is None:
        raise _not_found()
    if not await repo.has_read(conn, case_id, body.to_page_no, scope.person_id):
        raise blind_read()
    if before["superseded_by_page_id"] != target:
        raise ApiError("conflict", "A fact moves only to the clearer copy of its page.", 409)
    words = await repo.page_words(conn, _crypto(request), case_id, target)
    if not before["value"] or not provenance.supported(before["value"], body.word_ids, words):
        raise _invalid()
    after = await repo.change_fact(
        conn,
        fact_id,
        version,
        status=before["status"],
        value=before["value"],
        word_ids=body.word_ids,
        page_id=target,
        bbox=await repo.word_box(conn, target, body.word_ids),
        fields=before["fields"],
        event_date=before["event_date"],
    )
    out = _changed(response, after)
    assert after is not None
    await repo.log_check(conn, case_id, scope.person_id, "move", before, after, new_page_id=target)
    return out


@router.post("/facts/{fact_id}/flag-point", response_model=FlagOut)
async def flag_point(
    case_id: uuid.UUID, fact_id: uuid.UUID, access: Verify, conn: DbConn, scope: CurrentScope
) -> FlagOut:
    """Send a checked fact to Points against (slice E reads it through core/contracts/fact_flags)."""
    found = await repo.fact(conn, case_id, fact_id)
    if found is None:
        raise _fact_not_found()
    if found["status"] not in _CHECKED:
        raise ApiError("conflict", "Check this suggestion first.", 409)
    await repo.set_flag(conn, case_id, fact_id, POINTS, scope.person_id, on=True)
    return FlagOut(fact_id=fact_id, flagged=True)


@router.delete("/facts/{fact_id}/flag-point", response_model=FlagOut)
async def unflag_point(
    case_id: uuid.UUID, fact_id: uuid.UUID, access: Verify, conn: DbConn, scope: CurrentScope
) -> FlagOut:
    if await repo.fact(conn, case_id, fact_id) is None:
        raise _fact_not_found()
    await repo.set_flag(conn, case_id, fact_id, POINTS, scope.person_id, on=False)
    return FlagOut(fact_id=fact_id, flagged=False)


def _summary(r: dict) -> dict:  # type: ignore[type-arg]
    return {k: r[k] for k in ("source_type", "read", "in_period", "unreadable", "duplicate_of_page_no")}
