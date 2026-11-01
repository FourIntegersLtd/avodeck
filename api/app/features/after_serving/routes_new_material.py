"""Screen 21: records added since the report was served, and the other side's expert report.

New pages keep slice A's numbering and stay behind slice B's blind read (rule 3): what T-21a says may
matter about a page is returned only once the person has read that page. The other expert's claims are
checked by code against the cited page's words first (rule 2), the rest by T-21c (the `verify_opposing`
job). Each claim is shown with both quotes and page numbers; nothing is a discrepancy until the expert
confirms it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import Response
from pydantic import BaseModel, Field

from app.core import audit
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.after_serving.common import (
    CLAIM,
    CLAIM_VALUE,
    Crypto,
    Queued,
    Reads,
    Upload,
    View,
    Write,
    conflict,
    fetch_all,
    fetch_one,
    invalid,
    not_found,
    page_words,
    paragraphs,
    person_id,
    served_version,
    unseal,
)
from app.features.after_serving.router import router

ClaimResult = Literal["matches", "wrong_page", "wrong_value", "not_on_page", "contradicted"]
_CLAIM = (
    "id, document_id, claim_no, paragraph_ref, text_verbatim, cited_page_ref, cited_value, status, checked_by,"
    " evidence_page_id, evidence_word_ids, reason, expert_confirmed, confirmed_at, version"
)


# --- new since served -----------------------------------------------------------------------------------


class MayMatter(BaseModel):
    fact_id: uuid.UUID
    question_id: uuid.UUID
    why: str


class NewPage(BaseModel):
    page_id: uuid.UUID
    page_no: int
    read: bool
    may_matter: list[MayMatter]


class NewSinceServed(BaseModel):
    served_at: datetime | None
    pages: list[NewPage]
    first_page: int | None = None
    last_page: int | None = None
    read_count: int = 0


@router.get("/cases/{case_id}/new-since-served", response_model=NewSinceServed, response_model_exclude_unset=True)
async def new_since_served(case_id: uuid.UUID, access: View, conn: DbConn, reads: Reads) -> NewSinceServed:
    cur = await conn.execute("select app.after_serving_served_at(%s)", (case_id,))
    row = await cur.fetchone()
    served_at = row[0] if row else None
    if served_at is None:
        return NewSinceServed(served_at=None, pages=[])
    rows = await fetch_all(
        conn,
        "select id, bundle_page_no from app.pages"
        " where bundle_id = (select bundle_id from app.cases where id = %s)"
        " and created_at > %s and bundle_page_no is not null"
        " order by bundle_page_no",
        (case_id, served_at),
    )
    me = await person_id(conn)
    flagged: dict[uuid.UUID, list[MayMatter]] = {}
    for m in await fetch_all(
        conn,
        "select page_id, fact_id, question_id, why from app.may_matter where case_id = %s order by created_at, id",
        (case_id,),
    ):
        flagged.setdefault(m["page_id"], []).append(MayMatter(**m))
    pages = []
    for r in rows:
        page_no = int(r["bundle_page_no"])
        read = bool(reads is not None and me is not None and await reads.has_read(conn, case_id, page_no, me))
        pages.append(
            NewPage(page_id=r["id"], page_no=page_no, read=read, may_matter=flagged.get(r["id"], []) if read else [])
        )
    numbers = [p.page_no for p in pages]
    return NewSinceServed(
        served_at=served_at,
        pages=pages,
        first_page=min(numbers) if numbers else None,
        last_page=max(numbers) if numbers else None,
        read_count=sum(1 for p in pages if p.read),
    )


@router.post("/cases/{case_id}/new-since-served/check", status_code=202, response_model=Queued)
async def check_new_pages(case_id: uuid.UUID, access: Upload, conn: DbConn) -> Queued:
    return Queued(job_id=await registry.enqueue(conn, case_id, "find_may_matter"))


# --- the other expert's report --------------------------------------------------------------------------


class NewOpposingReport(BaseModel):
    document_id: uuid.UUID
    received_on: date
    expert_name: str | None = Field(default=None, max_length=200)
    expert_role: str | None = Field(default=None, max_length=200)


class OpposingQueued(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID


class OpposingClaimOut(BaseModel):
    id: uuid.UUID
    claim_no: int
    paragraph_ref: str | None
    text_verbatim: str
    their_quote: str | None
    their_page_ref: str | None
    our_quote: str | None
    our_page_no: int | None
    status: str
    checked_by: str | None
    reason: str | None
    expert_confirmed: bool
    confirmed_at: datetime | None
    discrepancy: bool
    version: int
    needs_attention: bool = Field(description="Checked, does not match and the expert has not yet confirmed it.")


class OwnParagraph(BaseModel):
    paragraph_id: uuid.UUID
    para_no: int
    text: str


class TheirParagraph(BaseModel):
    paragraph_ref: str
    text_verbatim: str


class Position(BaseModel):
    question_id: uuid.UUID
    question_no: int | None = None
    own: list[OwnParagraph]
    theirs: list[TheirParagraph]


class OpposingReport(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    received_on: date
    expert_name: str | None = None
    expert_role: str | None = None
    pages: int | None = None
    checked_at: datetime | None
    claims_found: int | None
    claims: list[OpposingClaimOut]
    positions: list[Position]
    matching_claims: list[OpposingClaimOut]
    other_claims: list[OpposingClaimOut]
    needs_attention_count: int


class OpposingClaimChange(BaseModel):
    confirm: bool | None = None
    status: ClaimResult | None = None


@router.post("/cases/{case_id}/opposing-reports", status_code=202, response_model=OpposingQueued)
async def register_opposing_report(
    case_id: uuid.UUID, body: NewOpposingReport, access: Upload, conn: DbConn
) -> OpposingQueued:
    if (
        await fetch_one(
            conn,
            "select 1 from app.documents where id = %s and bundle_id = (select bundle_id from app.cases where id = %s)",
            (body.document_id, case_id),
        )
        is None
    ):
        raise invalid("document_id")
    if await fetch_one(conn, "select 1 from app.opposing_reports where document_id = %s", (body.document_id,)):
        raise conflict("already_registered", "This report is already being checked.")
    job_id = await registry.enqueue(conn, case_id, "verify_opposing")
    row = await fetch_one(
        conn,
        "insert into app.opposing_reports (case_id, document_id, received_on, job_id, created_by, expert_name,"
        " expert_role) values (%s, %s, %s, %s, app.current_person_id(), %s, %s) returning id",
        (case_id, body.document_id, body.received_on, job_id, body.expert_name, body.expert_role),
    )
    assert row is not None
    return OpposingQueued(id=row["id"], job_id=job_id)


READ_FIRST = "read_first"  # shown in place of the check's result until the viewer has read our page (rule 3)


async def _evidence(conn: Any, case_id: uuid.UUID, row: dict[str, Any], reads: Any) -> tuple[int | None, bool]:
    """Our page's number for a claim's evidence, and whether this viewer has read it (no evidence: nothing to read)."""
    if row["evidence_page_id"] is None:
        return None, True
    page = await fetch_one(conn, "select bundle_page_no from app.pages where id = %s", (row["evidence_page_id"],))
    page_no = int(page["bundle_page_no"]) if page and page["bundle_page_no"] is not None else None
    me = await person_id(conn)
    if page_no is None or reads is None or me is None:
        return page_no, False
    return page_no, bool(await reads.has_read(conn, case_id, page_no, me))


async def claim_out(conn: Any, crypto: Any, case_id: uuid.UUID, row: dict[str, Any], reads: Any) -> OpposingClaimOut:
    our_quote: str | None = None
    our_page_no, read = await _evidence(conn, case_id, row, reads)
    text = await unseal(conn, crypto, case_id, row["text_verbatim"], CLAIM) or ""
    their_quote = await unseal(conn, crypto, case_id, row["cited_value"], CLAIM_VALUE)
    if not read:
        return OpposingClaimOut(
            id=row["id"],
            claim_no=row["claim_no"],
            paragraph_ref=row["paragraph_ref"],
            text_verbatim=text,
            their_quote=their_quote,
            their_page_ref=row["cited_page_ref"],
            our_quote=None,
            our_page_no=our_page_no,
            status=READ_FIRST,
            checked_by=None,
            reason=None,
            expert_confirmed=row["expert_confirmed"],
            confirmed_at=row["confirmed_at"],
            discrepancy=False,
            version=row["version"],
            needs_attention=False,
        )
    if row["evidence_page_id"] is not None:
        wanted = set(row["evidence_word_ids"] or [])
        if wanted:
            words = await page_words(conn, crypto, case_id, row["evidence_page_id"])
            our_quote = " ".join(text for wid, text in words if wid in wanted) or None
    return OpposingClaimOut(
        id=row["id"],
        claim_no=row["claim_no"],
        paragraph_ref=row["paragraph_ref"],
        text_verbatim=text,
        their_quote=their_quote,
        their_page_ref=row["cited_page_ref"],
        our_quote=our_quote,
        our_page_no=our_page_no,
        status=row["status"],
        checked_by=row["checked_by"],
        reason=row["reason"],
        expert_confirmed=row["expert_confirmed"],
        confirmed_at=row["confirmed_at"],
        discrepancy=bool(row["expert_confirmed"] and row["status"] != "matches"),
        version=row["version"],
        needs_attention=not row["expert_confirmed"] and row["status"] not in ("matches", READ_FIRST, "unchecked"),
    )


async def _positions(
    conn: Any, crypto: Any, case_id: uuid.UUID, stored: Any, claims: list[OpposingClaimOut]
) -> list[Position]:
    served = await served_version(conn, case_id)
    own = {p.id: p for p in (await paragraphs(conn, crypto, case_id, served) if served else [])}
    theirs: dict[str, str] = {}
    for c in claims:
        if c.paragraph_ref is not None:
            theirs.setdefault(c.paragraph_ref, c.text_verbatim)
    numbers = {
        r["id"]: int(r["number"])
        for r in await fetch_all(conn, "select id, number from app.questions where case_id = %s", (case_id,))
    }
    out = []
    for p in stored or []:
        out.append(
            Position(
                question_id=p["question_id"],
                question_no=numbers.get(uuid.UUID(str(p["question_id"]))),
                own=[
                    OwnParagraph(paragraph_id=own[pid].id, para_no=own[pid].para_no, text=own[pid].text)
                    for pid in (uuid.UUID(str(x)) for x in p.get("own_paragraph_ids", []))
                    if pid in own
                ],
                theirs=[
                    TheirParagraph(paragraph_ref=ref, text_verbatim=theirs[ref])
                    for ref in (str(x) for x in p.get("their_paragraph_ids", []))
                    if ref in theirs
                ],
            )
        )
    return out


@router.get("/cases/{case_id}/opposing-reports/{document_id}", response_model=OpposingReport)
async def opposing_report(
    case_id: uuid.UUID, document_id: uuid.UUID, access: View, conn: DbConn, crypto: Crypto, reads: Reads
) -> OpposingReport:
    report = await fetch_one(
        conn,
        "select r.id, r.document_id, r.received_on, r.expert_name, r.expert_role, d.pages, r.checked_at,"
        " r.claims_found, r.positions from app.opposing_reports r join app.documents d on d.id = r.document_id"
        " where r.case_id = %s and r.document_id = %s",
        (case_id, document_id),
    )
    if report is None:
        raise not_found()
    rows = await fetch_all(
        conn,
        f"select {_CLAIM} from app.opposing_claims where case_id = %s and document_id = %s order by claim_no",
        (case_id, document_id),
    )
    claims = [await claim_out(conn, crypto, case_id, r, reads) for r in rows]
    return OpposingReport(
        id=report["id"],
        document_id=report["document_id"],
        received_on=report["received_on"],
        expert_name=report["expert_name"],
        expert_role=report["expert_role"],
        pages=report["pages"],
        checked_at=report["checked_at"],
        claims_found=report["claims_found"],
        claims=claims,
        matching_claims=[c for c in claims if c.status == "matches"],
        other_claims=[c for c in claims if c.status != "matches"],
        needs_attention_count=sum(1 for c in claims if c.needs_attention),
        positions=await _positions(conn, crypto, case_id, report["positions"], claims),
    )


@router.patch("/cases/{case_id}/opposing-claims/{claim_id}", response_model=OpposingClaimOut)
async def change_claim(
    case_id: uuid.UUID,
    claim_id: uuid.UUID,
    body: OpposingClaimChange,
    access: Write,
    if_match: IfMatch,
    conn: DbConn,
    crypto: Crypto,
    reads: Reads,
    response: Response,
) -> OpposingClaimOut:
    row = await fetch_one(
        conn, f"select {_CLAIM} from app.opposing_claims where id = %s and case_id = %s", (claim_id, case_id)
    )
    if row is None:
        raise not_found()
    if row["version"] != if_match:
        raise stale()
    if row["status"] == "unchecked":
        raise conflict("not_checked", "This claim has not been checked yet.")
    if not (await _evidence(conn, case_id, row, reads))[1]:
        raise conflict(READ_FIRST, "Read that page first.")
    sets: list[str] = []
    params: list[Any] = []
    if body.status is not None and body.status != row["status"]:
        sets.append("status = %s")
        params.append(body.status)
    if body.confirm and not row["expert_confirmed"]:
        sets.append("expert_confirmed = true, confirmed_by = app.current_person_id(), confirmed_at = now()")
    if sets:
        updated = await fetch_one(
            conn,
            f"update app.opposing_claims set {', '.join(sets)} where id = %s returning {_CLAIM}",
            (*params, claim_id),
        )
        assert updated is not None
        if body.confirm and not row["expert_confirmed"]:
            await audit.record(
                conn, action="opposing_claim.confirmed", target=f"opposing_claim:{claim_id}", case_id=case_id
            )
        row = updated
    response.headers["etag"] = etag(row["version"])
    return await claim_out(conn, crypto, case_id, row, reads)
