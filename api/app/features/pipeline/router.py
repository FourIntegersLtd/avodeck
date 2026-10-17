"""Slice A's routes (architecture 5.2): bundle summary, duplicates, thumbnails, missing items.

- `GET /v1/cases/{id}/bundle/summary`: page counts by source, copies, unreadable pages, gaps, open
  missing items, each document's provenance and what each bundle version added.
- `PATCH /v1/cases/{id}/documents/{doc_id}`: how the records were obtained and when they arrived.
- `GET /v1/cases/{id}/bundle/versions`: what each delivery of records added.
- `POST /v1/cases/{id}/documents/append`: appended files form one new bundle version and their
  pages are processed (202 + job); files already cut into pages cannot be regrouped (409).
- `POST /v1/cases/{id}/instructing-party-requests/draft`: the pack's request wording listing the open
  missing items and the unreadable pages needing a clearer copy, with names, dates of birth and
  identifiers stripped. Plain code, no model.
- `POST /v1/cases/{id}/instructing-party-requests`: logs the text as sent (sealed, append-only) and
  returns a `mailto:` link; the open items it lists become `requested`.
- `POST /v1/cases/{id}/pages/{page_id}/quarantine` `{reason}`: "not this person's record". An
  assistant only suggests; the named expert confirms: the page keeps its number, leaves reading,
  extraction and the summary, and its facts are rejected.
- `DELETE /v1/cases/{id}/pages/{page_id}/quarantine` (named expert): dismisses a suggestion, or
  undoes a quarantine (verified facts are verified again; the page is read again, 202-style job).
- `POST /v1/cases/{id}/process` (202 + job): processes whatever is not done yet.
- `POST /v1/cases/{id}/process/cancel`: stops processing at the next page boundary; finished pages
  are kept and the job ends `paused` with reason `cancelled`.
- `POST /v1/cases/{id}/reprocess` (202 + job): the opt-in to extract every page again with the
  cell's current model; earlier unverified suggestions are superseded, verified facts never change.

- `GET /v1/cases/{id}/duplicates`: each page set aside as a copy, the page it copies and its review.
- `PATCH /v1/cases/{id}/pages/{page_id}/duplicate` (If-Match): `confirm` keeps it set aside; `undo`
  restores the page with everything found on it, and it is never set aside again (F13).
- `GET /v1/cases/{id}/pages/{page_no}/thumbnail`: the page's small picture, never cached.
- `GET /v1/cases/{id}/missing-items`: documents pages mention that the bundle does not hold.
- `PATCH /v1/cases/{id}/missing-items/{item_id}` (If-Match): `not_available` with a reason, `open`
  again, or `received` naming a page added after the item was listed (state-machines "Missing
  item"); `requested` is set only by sending a request (08b).
"""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, Request, Response

from app.core import audit
from app.core.ai import pii
from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.pipeline import PROCESS_BUNDLE, PROCESS_PAGES
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import CurrentScope, DbConn, IfMatch, etag, router, stale
from app.core.storage import SupabaseStorage
from app.features.pipeline import missing, paths, repo, wiring
from app.features.pipeline.schemas import (
    Appended,
    AppendRecords,
    BundleSummary,
    BundleVersion,
    BundleVersions,
    Document,
    DuplicatePage,
    DuplicateReview,
    Duplicates,
    JobQueued,
    MissingItem,
    MissingItemChange,
    MissingItems,
    ProcessingCancelled,
    ProvenanceChange,
    QuarantineAsk,
    QuarantinePage,
    RequestDraft,
    RequestDraftAsk,
    RequestLogged,
    RequestSent,
    SourceCount,
)
from app.features.pipeline.service import REPROCESS_CASE

pipeline_router = router(prefix="/v1/cases", tags=["pipeline"])

_REVIEW = {"confirm": ("confirmed", "page.duplicate_confirmed"), "undo": ("kept", "page.duplicate_undone")}


def _quarantine(q: repo.Quarantine, job_id: uuid.UUID | None = None) -> QuarantinePage:
    return QuarantinePage(page_id=q.page_id, page_no=q.page_no, status=q.status, reason=q.reason, job_id=job_id)  # type: ignore[arg-type]


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
    access: Annotated[CaseAccess, Depends(require_case_access("page.review_duplicate"))],
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


_MISSING_AUDIT = {
    "open": "missing_item.reopened",
    "not_available": "missing_item.not_available",
    "received": "missing_item.received",
}


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = getattr(request.app.state, "crypto", None)
    if crypto is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    return crypto


async def _missing_item(conn: DbConn, crypto: CaseCrypto, case_id: uuid.UUID, item: repo.MissingItem) -> MissingItem:
    description = await crypto.decrypt(
        conn, case_id, item.description, purpose=paths.missing_description_purpose(item.id)
    )
    reason = None
    if item.reason is not None:
        reason = (
            await crypto.decrypt(conn, case_id, item.reason, purpose=paths.missing_reason_purpose(item.id))
        ).decode()
    return MissingItem(
        id=item.id,
        citing_page_id=item.citing_page_id,
        citing_page_no=item.citing_page_no,
        word_ids=item.word_ids,
        description=description.decode(),
        reference_date=item.reference_date,
        status=item.status,  # type: ignore[arg-type]
        reason=reason,
        received_page_id=item.received_page_id,
        received_page_no=item.received_page_no,
        version=item.version,
    )


@pipeline_router.get("/{case_id}/missing-items", response_model=MissingItems)
async def list_missing_items(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.view_content"))],
    request: Request,
    conn: DbConn,
    response: Response,
) -> MissingItems:
    crypto = _crypto(request)
    items = [await _missing_item(conn, crypto, case_id, i) for i in await repo.missing_items(conn, case_id)]
    response.headers["cache-control"] = "private, no-store"
    return MissingItems(missing_items=items)


@pipeline_router.patch("/{case_id}/missing-items/{item_id}", response_model=MissingItem)
async def change_missing_item(
    case_id: uuid.UUID,
    item_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("request.draft"))],
    version: IfMatch,
    body: MissingItemChange,
    request: Request,
    conn: DbConn,
    scope: CurrentScope,
    response: Response,
) -> MissingItem:
    crypto = _crypto(request)
    current = await repo.missing_item(conn, case_id, item_id)
    if current is None:
        raise _not_found()
    if current.version != version:
        raise stale()
    if body.status not in missing.NEXT[current.status]:
        raise ApiError("conflict", "This item cannot change that way.", 409)
    reason: bytes | None = None
    received: uuid.UUID | None = None
    if body.status == "not_available":
        given = (body.reason or "").strip()
        if not given:
            raise ApiError("invalid", "Say why the document is not available.", 422)
        reason = await crypto.encrypt(conn, case_id, given.encode(), purpose=paths.missing_reason_purpose(item_id))
    if body.status == "received":
        if body.page_id is None:
            raise ApiError("invalid", "Choose the page that holds the document.", 422)
        if not await repo.page_added_after(conn, case_id, body.page_id, current.created_at):
            raise ApiError("conflict", "That page was not added after the item was listed.", 409)
        received = body.page_id
    moved = await repo.change_missing(
        conn,
        item_id,
        status=body.status,
        reason=reason,
        received_page_id=received,
        person_id=scope.person_id,
        version=version,
    )
    if moved is None:
        raise stale()
    await audit.record(conn, action=_MISSING_AUDIT[body.status], target=f"missing_item:{item_id}", case_id=case_id)
    after = await repo.missing_item(conn, case_id, item_id)
    assert after is not None
    response.headers["etag"] = etag(moved)
    response.headers["cache-control"] = "private, no-store"
    return await _missing_item(conn, crypto, case_id, after)


def _document(d: repo.DocumentRow) -> Document:
    return Document(
        id=d.id,
        kind=d.kind,
        filename=d.filename,
        pages=d.pages,
        first_page_no=d.first_page_no,
        last_page_no=d.last_page_no,
        received_on=d.received_on,
        obtained_how=d.obtained_how,
        bundle_version=d.bundle_version,
    )


@pipeline_router.get("/{case_id}/bundle/summary", response_model=BundleSummary)
async def bundle_summary(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.view_content"))],
    conn: DbConn,
    response: Response,
) -> BundleSummary:
    sources = await repo.source_counts(conn, case_id)
    copies, unreadable, gaps, open_items = await repo.summary_counts(conn, case_id)
    docs = await repo.documents(conn, case_id)
    held = await repo.quarantines(conn, case_id)
    response.headers["cache-control"] = "private, no-store"
    return BundleSummary(
        pages=sum(n for _, n in sources),
        by_source=[SourceCount(source_type=k, pages=n) for k, n in sources],
        duplicates=copies,
        unreadable=unreadable,
        gaps=gaps,
        missing_items=open_items,
        documents=[_document(d) for d in docs],
        versions=[BundleVersion(**v) for v in repo.versions_of(docs)],
        quarantine_suggestions=[_quarantine(q) for q in held if q.status == "suggested"],
        quarantined=[_quarantine(q) for q in held if q.status == "confirmed"],
    )


@pipeline_router.get("/{case_id}/bundle/versions", response_model=BundleVersions)
async def bundle_versions(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.view_content"))],
    conn: DbConn,
) -> BundleVersions:
    return BundleVersions(versions=[BundleVersion(**v) for v in repo.versions_of(await repo.documents(conn, case_id))])


@pipeline_router.patch("/{case_id}/documents/{document_id}", response_model=Document)
async def change_provenance(
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.upload"))],
    body: ProvenanceChange,
    conn: DbConn,
    response: Response,
) -> Document:
    if await repo.document(conn, case_id, document_id) is None:
        raise _not_found()
    changes = body.model_dump(exclude_unset=True)
    if changes:
        await repo.change_provenance(conn, document_id, changes)
        await audit.record(
            conn, action="document.provenance_changed", target=f"document:{document_id}", case_id=case_id
        )
    after = await repo.document(conn, case_id, document_id)
    assert after is not None
    response.headers["cache-control"] = "private, no-store"
    return _document(after)


@pipeline_router.post("/{case_id}/documents/append", status_code=202, response_model=Appended)
async def append_records(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.upload"))],
    body: AppendRecords,
    conn: DbConn,
) -> Appended:
    ids = sorted(set(body.document_ids))
    found, split = await repo.appendable(conn, case_id, ids)
    if found != len(ids):
        raise _not_found()
    if split:
        raise ApiError("conflict", "These records are already in the bundle.", 409)
    version = await repo.group_version(conn, case_id, ids)
    job_id = await wiring.registry().enqueue(conn, case_id, PROCESS_PAGES)
    await audit.record(conn, action="bundle.appended", target=f"case:{case_id}", case_id=case_id)
    return Appended(job_id=job_id, bundle_version=version)


@pipeline_router.post("/{case_id}/instructing-party-requests/draft", response_model=RequestDraft)
async def draft_request(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("request.draft"))],
    body: RequestDraftAsk,
    request: Request,
    conn: DbConn,
    response: Response,
) -> RequestDraft:
    crypto = _crypto(request)
    services = wiring.current()
    pack = services.packs.for_case(await repo.case_codes(conn, case_id))
    who = await services.identity(conn, case_id)

    def clean(text: str) -> str:
        return pii.strip(text, pack=pack, names=who.names, dates_of_birth=who.dates_of_birth).text

    items = await repo.open_missing(conn, case_id)
    lines = []
    for item in items:
        words = await crypto.decrypt(
            conn, case_id, item.description, purpose=paths.missing_description_purpose(item.id)
        )
        lines.append(f"- {clean(words.decode())} ({item.citing_page_no})")
    unreadable = await repo.clearer_copy_pages(conn, case_id)
    label, expert = await repo.request_wording_facts(conn, case_id)
    wording = pack.instructing_party_request(
        case_reference=clean(label),
        missing_items="\n".join(lines) or "-",
        clearer_copies="\n".join(f"- {n}" for _, n in unreadable) or "-",
        expert_name=expert,
    )
    response.headers["cache-control"] = "private, no-store"
    return RequestDraft(
        label=wording["label"],
        subject=clean(wording["subject"]),
        body=clean(wording["body"]),
        missing_item_ids=[i.id for i in items],
        clearer_copy_page_ids=[p for p, _ in unreadable],
        clearer_copy_page_nos=[n for _, n in unreadable],
    )


def _mailto(to: str | None, subject: str, body: str) -> str:
    """An RFC 6068 link: the address, then the subject and text percent-encoded."""
    return f"mailto:{quote(to or '', safe='@')}?subject={quote(subject, safe='')}&body={quote(body, safe='')}"


@pipeline_router.post("/{case_id}/instructing-party-requests", status_code=201, response_model=RequestLogged)
async def log_request(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("request.draft"))],
    body: RequestSent,
    request: Request,
    conn: DbConn,
    scope: CurrentScope,
    response: Response,
) -> RequestLogged:
    crypto = _crypto(request)
    items = sorted(set(body.missing_item_ids))
    pages = list(dict.fromkeys(body.clearer_copy_page_ids))
    if await repo.count_known(conn, "missing_items", case_id, items) != len(items):
        raise _not_found()
    if await repo.count_known(conn, "pages", case_id, pages) != len(pages):
        raise _not_found()
    request_id = uuid.uuid4()
    subject = await crypto.encrypt(
        conn, case_id, body.subject.encode(), purpose=paths.request_subject_purpose(request_id)
    )
    text = await crypto.encrypt(conn, case_id, body.body.encode(), purpose=paths.request_body_purpose(request_id))
    await repo.log_request(
        conn,
        case_id,
        request_id=request_id,
        missing_item_ids=items,
        page_ids=pages,
        subject=subject,
        body=text,
        follow_up_on=body.follow_up_on,
        person_id=scope.person_id,
    )
    await repo.mark_requested(conn, case_id, items, scope.person_id)
    await audit.record(
        conn,
        action="instructing_party_request.logged",
        target=f"instructing_party_request:{request_id}",
        case_id=case_id,
    )
    response.headers["cache-control"] = "private, no-store"
    return RequestLogged(id=request_id, mailto=_mailto(body.to, body.subject, body.body))


@pipeline_router.post("/{case_id}/process", status_code=202, response_model=JobQueued)
async def start_processing(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.upload"))],
    conn: DbConn,
) -> JobQueued:
    """Processes whatever is not done yet: finished pages are never read again."""
    return JobQueued(job_id=await wiring.registry().enqueue(conn, case_id, PROCESS_BUNDLE))


@pipeline_router.post("/{case_id}/process/cancel", status_code=202, response_model=ProcessingCancelled)
async def cancel_processing(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.upload"))],
    scope: CurrentScope,
    conn: DbConn,
) -> ProcessingCancelled:
    """Stops processing at the next page boundary; pages being read finish and are kept."""
    cancelled = await repo.request_cancel(conn, case_id, scope.person_id)
    if cancelled:
        await audit.record(conn, action="bundle.processing_cancelled", target=f"case:{case_id}", case_id=case_id)
    return ProcessingCancelled(cancelled=cancelled)


@pipeline_router.post("/{case_id}/reprocess", status_code=202, response_model=JobQueued)
async def reprocess(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.upload"))],
    scope: CurrentScope,
    conn: DbConn,
) -> JobQueued:
    """The opt-in to read every page again with the cell's current model; verified facts never change."""
    await repo.opt_in_reprocess(conn, case_id, scope.person_id)
    job_id = await wiring.registry().enqueue(conn, case_id, REPROCESS_CASE)
    await audit.record(conn, action="bundle.reprocess_requested", target=f"case:{case_id}", case_id=case_id)
    return JobQueued(job_id=job_id)


def _decides(access: CaseAccess) -> bool:
    """Only the named expert confirms, dismisses or undoes a quarantine (permissions.md)."""
    return "fact.verify" in access.allowed_actions


@pipeline_router.post("/{case_id}/pages/{page_id}/quarantine", response_model=QuarantinePage)
async def quarantine_page(
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("page.quarantine"))],
    body: QuarantineAsk,
    scope: CurrentScope,
    conn: DbConn,
) -> QuarantinePage:
    """An assistant's call suggests; the named expert's confirms (accepting any suggestion)."""
    if await repo.page_no_of(conn, case_id, page_id) is None:
        raise _not_found()
    live = await repo.live_quarantine(conn, case_id, page_id)
    if live is None:
        live = await repo.suggest_quarantine(conn, case_id, page_id, body.reason, scope.person_id)
        await audit.record(conn, action="page.quarantine_suggested", target=f"page:{page_id}", case_id=case_id)
    if not _decides(access) or live.status == "confirmed":
        return _quarantine(live)
    await repo.confirm_quarantine(conn, live, body.reason, scope.person_id)
    await audit.record(conn, action="page.quarantined", target=f"page:{page_id}", case_id=case_id)
    return QuarantinePage(page_id=page_id, page_no=live.page_no, status="confirmed", reason=body.reason)


@pipeline_router.delete("/{case_id}/pages/{page_id}/quarantine", response_model=QuarantinePage)
async def release_page(
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("page.quarantine"))],
    scope: CurrentScope,
    conn: DbConn,
) -> QuarantinePage:
    if not _decides(access):
        raise ApiError("forbidden", "You cannot do that on this case.", 403)
    live = await repo.live_quarantine(conn, case_id, page_id)
    if live is None:
        raise _not_found()
    status = await repo.close_quarantine(conn, live, scope.person_id)
    job_id = None
    if status == "undone":
        job_id = await wiring.registry().enqueue(conn, case_id, PROCESS_PAGES)
        await audit.record(conn, action="page.quarantine_undone", target=f"page:{page_id}", case_id=case_id)
    else:
        await audit.record(conn, action="page.quarantine_dismissed", target=f"page:{page_id}", case_id=case_id)
    return QuarantinePage(page_id=page_id, page_no=live.page_no, status=status, reason=live.reason, job_id=job_id)  # type: ignore[arg-type]
