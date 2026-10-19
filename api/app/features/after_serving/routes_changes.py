"""Screen 21b: correct or update a served report (rule 8).

The served version is shown read-only and is never changed. Each change to one of its paragraphs is a
correction or a change of opinion with the expert's reason; code lists the paragraphs that share its
citations and T-21e suggests others that may depend on it (the `find_dependents` job). The supplementary
report holds only the changes and the declaration, links to the served version and is signed with
step-up (rule 10), which locks it.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Response
from pydantic import BaseModel, Field

from app.core import audit
from app.core.contracts.citations import Citation
from app.core.contracts.profile import named_expert
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.after_serving import docs, supplementary
from app.features.after_serving.common import (
    CHANGE_NEW,
    CHANGE_OLD,
    CHANGE_REASON,
    Crypto,
    Pack,
    Queued,
    Reads,
    Sign,
    Storage,
    View,
    Write,
    check_citations,
    citations_json,
    conflict,
    fetch_all,
    fetch_one,
    invalid,
    not_found,
    paragraphs,
    report_file,
    seal,
    served_version,
    unseal,
)
from app.features.after_serving.router import router
from app.features.after_serving.routes_questions import AnswersSignedVersion, Signed, court_today, lock_version
from app.features.after_serving.words import long_date, word

Kind = Literal["correction", "change_of_opinion"]
_CHANGE = (
    "id, paragraph_id, kind, old_text, new_text, reason, citation_refs, dependent_paragraph_ids,"
    " suggested_dependents, supplementary_version_id, version"
)


# --- the served version ---------------------------------------------------------------------------------


class ServedVersion(BaseModel):
    id: uuid.UUID
    version_no: int
    display_name: str
    status: str
    served_on: date | None
    sha256: str | None


class ServedParagraph(BaseModel):
    id: uuid.UUID
    para_no: int
    text: str
    fact_ids: list[uuid.UUID]


class Served(BaseModel):
    version: ServedVersion | None
    paragraphs: list[ServedParagraph]


@router.get("/cases/{case_id}/report/served", response_model=Served)
async def served_report(case_id: uuid.UUID, access: View, conn: DbConn, crypto: Crypto) -> Served:
    version_id = await served_version(conn, case_id)
    if version_id is None:
        return Served(version=None, paragraphs=[])
    row = await fetch_one(
        conn,
        "select v.id, v.version_no, v.display_name, v.status, v.sha256, (select min(s.served_on)"
        " from app.report_servings s where s.report_version_id = v.id) as served_on"
        " from app.report_versions v where v.id = %s",
        (version_id,),
    )
    assert row is not None
    return Served(
        version=ServedVersion(**row),
        paragraphs=[
            ServedParagraph(id=p.id, para_no=p.para_no, text=p.text, fact_ids=p.fact_ids)
            for p in await paragraphs(conn, crypto, case_id, version_id)
        ],
    )


# --- changes --------------------------------------------------------------------------------------------


class NewChange(BaseModel):
    paragraph_id: uuid.UUID
    kind: Kind
    new_text: str = Field(min_length=1, max_length=50000)
    reason: str = Field(min_length=1, max_length=5000)
    citation_refs: list[Citation] = Field(default_factory=list, max_length=200)


class ChangeEdit(BaseModel):
    kind: Kind | None = None
    new_text: str | None = Field(default=None, min_length=1, max_length=50000)
    reason: str | None = Field(default=None, min_length=1, max_length=5000)
    citation_refs: list[Citation] | None = Field(default=None, max_length=200)


class Dependent(BaseModel):
    paragraph_id: uuid.UUID
    depends: bool
    reason: str


class ReportChangeOut(BaseModel):
    id: uuid.UUID
    paragraph_id: uuid.UUID
    kind: str
    old_text: str
    new_text: str
    reason: str
    citation_refs: list[Citation]
    dependent_paragraph_ids: list[uuid.UUID]
    suggested_dependents: list[Dependent]
    supplementary_version_id: uuid.UUID | None
    version: int


class Made(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID


class Supplementary(BaseModel):
    id: uuid.UUID
    version_no: int
    display_name: str
    status: str
    sha256: str | None
    prev_version_id: uuid.UUID | None
    locked_at: datetime | None
    signed_by: str | None = None
    signed_on: date | None = None


class ReportChanges(BaseModel):
    changes: list[ReportChangeOut]
    supplementary: Supplementary | None


async def change_out(conn: Any, crypto: Any, case_id: uuid.UUID, row: dict[str, Any]) -> ReportChangeOut:
    return ReportChangeOut(
        id=row["id"],
        paragraph_id=row["paragraph_id"],
        kind=row["kind"],
        old_text=await unseal(conn, crypto, case_id, row["old_text"], CHANGE_OLD) or "",
        new_text=await unseal(conn, crypto, case_id, row["new_text"], CHANGE_NEW) or "",
        reason=await unseal(conn, crypto, case_id, row["reason"], CHANGE_REASON) or "",
        citation_refs=row["citation_refs"],
        dependent_paragraph_ids=row["dependent_paragraph_ids"],
        suggested_dependents=row["suggested_dependents"],
        supplementary_version_id=row["supplementary_version_id"],
        version=row["version"],
    )


async def _served_or_conflict(conn: Any, case_id: uuid.UUID) -> uuid.UUID:
    served = await served_version(conn, case_id)
    if served is None:
        raise conflict("not_served", "The report has not been served yet.")
    return served


@router.post("/cases/{case_id}/report-changes", status_code=201, response_model=Made)
async def add_change(
    case_id: uuid.UUID, body: NewChange, access: Write, conn: DbConn, crypto: Crypto, reads: Reads, response: Response
) -> Made:
    served = await _served_or_conflict(conn, case_id)
    found = {p.id: p for p in await paragraphs(conn, crypto, case_id, served)}
    changed = found.get(body.paragraph_id)
    if changed is None:
        raise invalid("paragraph_id")
    await check_citations(conn, case_id, reads, body.citation_refs)
    shared = set(changed.fact_ids)
    dependents = [p.id for p in found.values() if p.id != changed.id and shared & set(p.fact_ids)]
    row = await fetch_one(
        conn,
        "insert into app.report_changes (case_id, served_version_id, kind, paragraph_id, old_text, new_text, reason,"
        " citation_refs, dependent_paragraph_ids, created_by) values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,"
        " app.current_person_id()) returning id, version",
        (
            case_id,
            served,
            body.kind,
            changed.id,
            await seal(conn, crypto, case_id, changed.text, CHANGE_OLD),
            await seal(conn, crypto, case_id, body.new_text, CHANGE_NEW),
            await seal(conn, crypto, case_id, body.reason, CHANGE_REASON),
            json.dumps(citations_json(body.citation_refs)),
            dependents,
        ),
    )
    assert row is not None
    await audit.record(conn, action="report_change.added", target=f"report_change:{row['id']}", case_id=case_id)
    response.headers["etag"] = etag(row["version"])
    return Made(id=row["id"], job_id=await registry.enqueue(conn, case_id, "find_dependents"))


async def _change(conn: Any, case_id: uuid.UUID, change_id: uuid.UUID) -> dict[str, Any]:
    row = await fetch_one(
        conn, f"select {_CHANGE} from app.report_changes where id = %s and case_id = %s", (change_id, case_id)
    )
    if row is None:
        raise not_found()
    return row


@router.get("/cases/{case_id}/report-changes/{change_id}", response_model=ReportChangeOut)
async def get_change(
    case_id: uuid.UUID, change_id: uuid.UUID, access: View, conn: DbConn, crypto: Crypto, response: Response
) -> ReportChangeOut:
    row = await _change(conn, case_id, change_id)
    response.headers["etag"] = etag(row["version"])
    return await change_out(conn, crypto, case_id, row)


async def _locked(conn: Any, version_id: uuid.UUID | None) -> bool:
    if version_id is None:
        return False
    row = await fetch_one(conn, "select status from app.report_versions where id = %s", (version_id,))
    return row is None or row["status"] != "draft"


@router.patch("/cases/{case_id}/report-changes/{change_id}", response_model=ReportChangeOut)
async def edit_change(
    case_id: uuid.UUID,
    change_id: uuid.UUID,
    body: ChangeEdit,
    access: Write,
    if_match: IfMatch,
    conn: DbConn,
    crypto: Crypto,
    reads: Reads,
    response: Response,
) -> ReportChangeOut:
    row = await _change(conn, case_id, change_id)
    if row["version"] != if_match:
        raise stale()
    if await _locked(conn, row["supplementary_version_id"]):
        raise conflict("locked", "This change is in a signed supplementary report.")
    sets: list[str] = []
    params: list[Any] = []
    if body.kind is not None:
        sets.append("kind = %s")
        params.append(body.kind)
    if body.new_text is not None:
        sets.append("new_text = %s")
        params.append(await seal(conn, crypto, case_id, body.new_text, CHANGE_NEW))
    if body.reason is not None:
        sets.append("reason = %s")
        params.append(await seal(conn, crypto, case_id, body.reason, CHANGE_REASON))
    if body.citation_refs is not None:
        await check_citations(conn, case_id, reads, body.citation_refs)
        sets.append("citation_refs = %s::jsonb")
        params.append(json.dumps(citations_json(body.citation_refs)))
    if sets:
        updated = await fetch_one(
            conn,
            f"update app.report_changes set {', '.join(sets)} where id = %s returning {_CHANGE}",
            (*params, change_id),
        )
        assert updated is not None
        row = updated
    response.headers["etag"] = etag(row["version"])
    return await change_out(conn, crypto, case_id, row)


_SUPPLEMENTARY = "id, version_no, display_name, status, sha256, prev_version_id, locked_at"


async def latest_supplementary(conn: Any, case_id: uuid.UUID, served: uuid.UUID) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        f"select {_SUPPLEMENTARY} from app.report_versions where case_id = %s and type = 'supplementary'"
        " and prev_version_id = %s order by version_no desc limit 1",
        (case_id, served),
    )


@router.get("/cases/{case_id}/report-changes", response_model=ReportChanges)
async def list_changes(case_id: uuid.UUID, access: View, conn: DbConn, crypto: Crypto, pack: Pack) -> ReportChanges:
    rows = await fetch_all(
        conn, f"select {_CHANGE} from app.report_changes where case_id = %s order by created_at, id", (case_id,)
    )
    served = await served_version(conn, case_id)
    latest = await latest_supplementary(conn, case_id, served) if served else None
    signer = await named_expert(conn, case_id) if latest and latest["locked_at"] else None
    return ReportChanges(
        changes=[await change_out(conn, crypto, case_id, r) for r in rows],
        supplementary=(
            Supplementary(
                **latest,
                signed_by=signer.full_name if signer else None,
                signed_on=latest["locked_at"].astimezone(ZoneInfo(pack.timezone)).date()
                if latest["locked_at"]
                else None,
            )
            if latest
            else None
        ),
    )


@router.post("/cases/{case_id}/report-changes/supplementary", status_code=202, response_model=Queued)
async def build_supplementary(case_id: uuid.UUID, access: Write, conn: DbConn) -> Queued:
    await _served_or_conflict(conn, case_id)
    open_changes = await fetch_one(
        conn,
        "select count(*) as n from app.report_changes c left join app.report_versions v on v.id ="
        " c.supplementary_version_id"
        " where c.case_id = %s and (v.id is null or v.status = 'draft')",
        (case_id,),
    )
    if open_changes is None or not open_changes["n"]:
        raise conflict("no_changes", "Add a change before building the supplementary report.")
    return Queued(job_id=await registry.enqueue(conn, case_id, "build_supplementary"))


class SupplementarySignIn(BaseModel):
    declaration_confirmed: bool


@router.post("/cases/{case_id}/report/versions/{version_id}/sign", response_model=Signed)
async def sign_supplementary(
    case_id: uuid.UUID,
    version_id: uuid.UUID,
    body: SupplementarySignIn,
    access: Sign,
    conn: DbConn,
    crypto: Crypto,
    storage: Storage,
    pack: Pack,
) -> Signed:
    if not body.declaration_confirmed:
        raise invalid("declaration_confirmed")
    row = await fetch_one(
        conn, "select type, status from app.report_versions where id = %s and case_id = %s", (version_id, case_id)
    )
    if row is None:
        raise not_found()
    if row["type"] != "supplementary" or row["status"] != "draft":
        raise conflict("locked", "This version is signed and does not change.")
    lang = pack.language
    blocks = await supplementary.blocks(conn, crypto, case_id, pack, version_id)
    blocks.append(("body", word(lang, "dated", date=long_date(lang, pack.date_order, court_today(pack.timezone)))))
    path = report_file(case_id, version_id, "supplementary-signed.docx")
    version = await lock_version(conn, storage, crypto, case_id, version_id, path, docs.build(blocks))
    await audit.record(conn, action="supplementary.signed", target=f"report_version:{version_id}", case_id=case_id)
    return Signed(version=AnswersSignedVersion(**version))
