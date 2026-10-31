"""Slice B routes: reading pages (S09). Every route checks case access first (rule 9).

The page itself never carries facts; `/facts` sits behind the blind-read gate (rule 3).
Responses carrying the page picture are never cached.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from typing import Annotated, Any, Literal

from fastapi import Depends, Query, Request, Response
from PIL import Image

from app.core.ai import provenance
from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts import served
from app.core.contracts.document_words import Correction, apply_corrections, document_words
from app.core.contracts.document_words import box as word_box
from app.core.contracts.fact_flags import POINTS, RECHECK
from app.core.contracts.pipeline import EntryPointMissing, extract_page_text, process_pages
from app.core.contracts.reading import blind_read, require_page_read
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import CurrentScope, DbConn, IfMatch, etag, stale
from app.core.http import router as make_router
from app.core.packs import PackLibrary
from app.core.storage import CaseFileStorage
from app.features.reader import fields as fact_fields
from app.features.reader import repo, timeline_date, today  # noqa: F401 (registers the Today providers)
from app.features.reader import search as bundle_search
from app.features.reader.schemas import (
    Box,
    FactList,
    FactOut,
    FlagOut,
    Highlight,
    JobOut,
    MoveIn,
    MyNote,
    MyNoteIn,
    MyNotes,
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
    SearchIn,
    SearchOut,
    SearchPage,
    TranscriptionIn,
    TurnIn,
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
    ("verified", "edit"): "edited",
    ("edited", "edit"): "edited",
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


@router.put("/pages/{page_no}/turn", response_model=PageDetail)
async def turn_page(
    case_id: uuid.UUID,
    page_no: int,
    body: TurnIn,
    access: Annotated[CaseAccess, Depends(require_case_access("page.rotate"))],
    conn: DbConn,
    scope: CurrentScope,
) -> PageDetail:
    """Turns the page for reading, at once. The picture, its words, their boxes and the facts stay as they
    were and nothing is read again: the screen turns the picture and what is drawn on it together."""
    r = await _page(conn, case_id, page_no, scope)
    await repo.set_turn(conn, r["id"], body.degrees, scope.person_id)
    return _detail(await _page(conn, case_id, page_no, scope))


SMALL_WIDTH = 240


def _small(picture: bytes) -> bytes:
    """A small copy of the page picture for the page strip (made in memory, never stored)."""
    with Image.open(io.BytesIO(picture)) as im:
        im.draft("RGB", (SMALL_WIDTH, SMALL_WIDTH * 4))  # decode a JPEG at a fraction of its size
        im.thumbnail((SMALL_WIDTH, SMALL_WIDTH * 4))
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=70)
    return out.getvalue()


@router.get("/pages/{page_no}/image", response_class=Response)
async def page_image(
    case_id: uuid.UUID,
    page_no: int,
    access: View,
    request: Request,
    conn: DbConn,
    scope: CurrentScope,
    size: Literal["page", "small"] = "page",
) -> Response:
    storage: CaseFileStorage | None = getattr(request.app.state, "storage", None)
    crypto: CaseCrypto | None = getattr(request.app.state, "crypto", None)
    if storage is None or crypto is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    r = await _page(conn, case_id, page_no, scope)
    if r["image_path"] is None:
        raise _not_found()
    path = r["image_path"]
    picture = await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path)
    if size == "small":
        picture = await asyncio.to_thread(_small, picture)  # off the event loop
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


def _dealt_with(f: dict[str, Any]) -> bool:
    if f["status"] == "rejected":
        return True
    return f["status"] in ("verified", "edited") and not f.get("needs_recheck")


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
    rows = await repo.page_facts(conn, case_id, r["id"])
    return FactList(
        facts=[FactOut(**f) for f in rows],
        total=len(rows),
        done=sum(1 for f in rows if _dealt_with(f)),
        highlights=[
            Highlight(id=f["id"], word_ids=f["word_ids"], bbox=f["bbox"]) for f in rows if f["status"] != "rejected"
        ],
        reassigned=await repo.page_reassigned(conn, case_id, r["id"]),
    )


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
    read_now = await repo.page_words(conn, _crypto(request), case_id, r["id"])
    reading = " ".join(t for t in read_now.values() if t)
    return PageWords(words=[PageWord(**w) for w in words], reading=reading)


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


def _served() -> ApiError:
    # Rule 8: a fact a served report cites changes only through a supplementary report (screen 21b).
    return ApiError("served_use_supplementary", "This is in your served report. Use a supplementary report.", 409)


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


def _run(words: dict[str, str], cited: list[str], corrected: list[str]) -> list[str]:
    """The corrected words in reading order: 422 unless they are among the cited ones and one unbroken run
    on the page (decision 0018, rule 2)."""
    order = {w: i for i, w in enumerate(words)}
    if not set(corrected) <= set(cited) or any(w not in order for w in corrected):
        raise _invalid()
    run = sorted(set(corrected), key=order.__getitem__)
    if order[run[-1]] - order[run[0]] != len(run) - 1:
        raise _invalid()
    return run


async def _shows(
    request: Request,
    conn: DbConn,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    value: str,
    word_ids: list[str],
    *,
    corrected: tuple[list[str], str] | None = None,
) -> Correction | None:
    """422 unless the cited words show the value (rule 2), read as `cited_words` reads them, with a new
    reading of `corrected` replacing any it overlaps. Returns that new reading, its words in reading order."""
    words, corrections = await repo.page_reading(conn, _crypto(request), case_id, page_id)
    new: Correction | None = None
    if corrected is not None:
        new = (_run(words, word_ids, corrected[0]), corrected[1])
        corrections = repo.replacing(corrections, new)
    if not provenance.supported(value, word_ids, apply_corrections(words, corrections, cited=word_ids)):
        raise _invalid()
    return new


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
        await _shows(request, conn, case_id, before["page_id"], value, word_ids)
    if (
        body.action in ("edit", "not_right")
        and before["status"] in _CHECKED
        and await served.cited_in_served(conn, case_id, fact_id)
    ):
        raise _served()
    new_reading: Correction | None = None
    if rechecking and body.action == "edit" and not body.word_ids:
        raise ApiError("words_needed", "Choose the words on the page that show this.", 422)
    if body.action == "edit":
        if body.new_value is None and body.fields is None and "event_date" not in body.model_fields_set:
            raise _invalid()
        if body.corrected_words is not None and body.new_value is None:
            raise _invalid()
        if body.new_value is not None:
            if not body.new_value.strip():
                raise _invalid()
            value, word_ids = body.new_value, body.word_ids or before["word_ids"]
            corrected = body.corrected_words
            new_reading = await _shows(
                request,
                conn,
                case_id,
                before["page_id"],
                value,
                word_ids,
                corrected=None if corrected is None else (corrected.word_ids, corrected.text),
            )
            if body.word_ids or value != before["value"] or word_ids != before["word_ids"]:
                bbox = await repo.word_box(conn, before["page_id"], word_ids)
        elif body.word_ids is not None:
            word_ids = body.word_ids
            await _shows(request, conn, case_id, before["page_id"], value, word_ids)
            bbox = await repo.word_box(conn, before["page_id"], word_ids)
        if body.fields is not None:
            await _check_fields(request, conn, case_id, before["kind"], body.fields)
            fields = body.fields
        if "event_date" in body.model_fields_set:
            event_date = body.event_date
    date_basis = None
    if before["event_date"] is None and event_date is None and body.action in ("correct", "edit"):
        library: PackLibrary = request.app.state.packs
        resolved, basis = await timeline_date.resolve_for_verify(
            conn,
            _crypto(request),
            library,
            case_id,
            before["page_id"],
            before["kind"],
            fields,
            word_ids,
        )
        if resolved is not None:
            event_date = resolved
            date_basis = basis
    elif before["event_date"] is None and event_date is not None and "event_date" in body.model_fields_set:
        from app.core.contracts.fact_dates import DATE_BASIS_PRINTED

        date_basis = DATE_BASIS_PRINTED
    correction_id, touched = None, []
    if new_reading is not None:
        # The words whose reading changes: the new run and every reading it replaces. Rule 8: none may be
        # cited by a fact in a served report. Saved before the change, so the fact shows it cites it.
        touched = await repo.corrected_words_near(conn, before["page_id"], new_reading[0])
        if await repo.served_fact_cites(conn, case_id, before["page_id"], touched):
            raise _served()
        correction_id = await repo.save_correction(
            conn, _crypto(request), case_id, before["page_id"], new_reading[0], new_reading[1], scope.person_id
        )
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
        date_basis=date_basis,
    )
    out = _changed(response, after)
    assert after is not None
    if new_reading is not None:
        await repo.flag_unsupported(
            conn, _crypto(request), case_id, before["page_id"], touched, scope.person_id, except_fact=fact_id
        )
    elif body.action in ("correct", "edit") and after["value"] is not None:
        correction_id = await repo.correction_cited(conn, before["page_id"], after["word_ids"])
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
        correction_id=correction_id,
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
    if await served.cited_in_served(conn, case_id, fact_id):
        raise _served()
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


@router.post("/search", response_model=SearchOut)
async def search_bundle(
    case_id: uuid.UUID, body: SearchIn, access: View, request: Request, response: Response, conn: DbConn
) -> SearchOut:
    """Find words in the bundle: the words read from the pages (with the expert's readings of misread
    words), never the suggestions, so the blind read stays (rule 3). Not reading. Answers page numbers,
    hit counts and places only; the words are never returned or logged (rule 6)."""
    response.headers["cache-control"] = "private, no-store"
    wanted = bundle_search.terms(body.q)
    pages = await repo.searchable_pages(conn, case_id)
    polygons, sizes = await repo.word_places(conn, case_id)
    crypto = _crypto(request)
    by_page: dict[uuid.UUID, list[tuple[str, str]]] = {}
    for document_id in sorted({d for _, d in pages.values()}, key=str):
        for key, text in await document_words(conn, crypto, case_id, document_id):
            page_part, word_id = key.split("/", 1)
            page_id = uuid.UUID(page_part)
            if page_id in pages:
                by_page.setdefault(page_id, []).append((word_id, text))
    found: list[SearchPage] = []
    for page_id, words in by_page.items():
        hits = bundle_search.find(words, wanted)
        if not hits:
            continue
        places = polygons.get(page_id, {})
        width, height = sizes.get(page_id) or _furthest(places)
        boxes = []
        for ids in hits:
            placed = [w for w in ids if any(places.get(w, []))]
            if placed:
                boxes.append(Box(**word_box(placed, places, width, height)))
        found.append(SearchPage(page_no=pages[page_id][0], hits=len(hits), boxes=boxes))
    found.sort(key=lambda p: p.page_no)
    return SearchOut(pages=found, total_hits=sum(p.hits for p in found))


def _furthest(polygons: dict[str, list[float]]) -> tuple[float, float]:
    """A page with no analysis: its size from its furthest word (as `repo._size`)."""
    width = max((x for p in polygons.values() for x in p[0::2]), default=1.0) or 1.0
    height = max((y for p in polygons.values() for y in p[1::2]), default=1.0) or 1.0
    return width, height


NoteKeeper = Annotated[CaseAccess, Depends(require_case_access("page.read"))]


@router.put("/pages/{page_no}/my-note", response_model=MyNote)
async def keep_my_note(
    case_id: uuid.UUID,
    page_no: int,
    body: MyNoteIn,
    access: NoteKeeper,
    request: Request,
    response: Response,
    conn: DbConn,
    scope: CurrentScope,
) -> MyNote:
    """The person's own bookmark and private note for the page; only they see it. Separate from the
    record: not reading, never sent to AI, never logged."""
    response.headers["cache-control"] = "private, no-store"
    page_id = await repo.page_id_by_no(conn, case_id, page_no)
    if page_id is None:
        raise _not_found()
    note = body.note.strip()
    kept = await repo.keep_note(conn, _crypto(request), case_id, page_id, scope.person_id, body.bookmarked, note)
    if kept is None:
        return MyNote(page_no=page_no, bookmarked=False, note="", updated_at=None)
    return MyNote(page_no=page_no, bookmarked=body.bookmarked, note=note, updated_at=kept)


@router.get("/my-notes", response_model=MyNotes)
async def my_notes(
    case_id: uuid.UUID, access: NoteKeeper, request: Request, response: Response, conn: DbConn, scope: CurrentScope
) -> MyNotes:
    """The person's own bookmarks and notes on the case, in page order."""
    response.headers["cache-control"] = "private, no-store"
    notes = await repo.my_notes(conn, _crypto(request), case_id, scope.person_id)
    return MyNotes(notes=[MyNote(**n) for n in notes])


def _summary(r: dict) -> dict:  # type: ignore[type-arg]
    return {k: r[k] for k in ("source_type", "read", "in_period", "unreadable", "duplicate_of_page_no", "turn")}


from app.features.reader import (  # noqa: E402
    second_reading_routes,  # noqa: E402,F401  (adds the second-reading routes)
    transcript_routes,  # noqa: E402,F401  (adds the transcript routes to `router`)
)
