"""Screen 13: things to look at (architecture 5.2 "13 Things to look at", slice D brief).

Open findings with the pages they rest on. Only the named expert decides (`finding.decide`); "not
relevant" and "explained elsewhere" need a reason. Nothing closes a finding on its own: a re-run marks
findings no longer found as gone and keeps decisions on the ones still found. A decided finding that is
gone stays listed (`status: gone`, "no longer found") so the expert's decision never vanishes.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import Depends, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, model_validator

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.http import DbConn, etag
from app.core.jobs import registry
from app.features.understand import jobs  # noqa: F401  (registers rerun_checks)
from app.features.understand.common import not_found
from app.features.understand.router import router

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Decide = Annotated[CaseAccess, Depends(require_case_access("finding.decide"))]

Decision = Literal["relevant", "not_relevant", "explained_elsewhere"]
NEEDS_REASON = {"not_relevant", "explained_elsewhere"}


class FindingOut(BaseModel):
    id: uuid.UUID
    check_key: str
    rule: str
    gentle: bool
    evidence_fact_ids: list[uuid.UUID]
    evidence_pages: list[int]
    decision: str | None
    reason: str | None
    version: int
    status: Literal["open", "gone"] = "open"


class Findings(BaseModel):
    findings: list[FindingOut]


class FindingDecision(BaseModel):
    decision: Decision
    reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _reason(self) -> FindingDecision:
        if self.decision in NEEDS_REASON and not (self.reason or "").strip():
            raise ValueError("say why")
        return self


class Queued(BaseModel):
    job_id: uuid.UUID


_F = (
    "select f.id, f.check_key, f.rule, f.gentle, f.evidence_fact_ids,"
    " array(select p.bundle_page_no from unnest(f.evidence_page_ids) with ordinality e(id, n)"
    "   join app.pages p on p.id = e.id order by e.n) as evidence_pages,"
    " f.decision, f.reason, f.version, f.status from app.findings f"
)
_SHOWN = "(f.status = 'open' or f.decision is not null)"


async def _one(conn: DbConn, case_id: uuid.UUID, finding_id: uuid.UUID) -> FindingOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{_F} where f.id = %s and f.case_id = %s and {_SHOWN}", (finding_id, case_id))
        row = await cur.fetchone()
    if row is None:
        raise not_found()
    return FindingOut(**row)


@router.get("/cases/{case_id}/findings", response_model=Findings)
async def list_findings(case_id: uuid.UUID, access: View, conn: DbConn) -> Findings:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"{_F} where f.case_id = %s and {_SHOWN} order by f.status desc, f.created_at, f.id", (case_id,)
        )
        rows = await cur.fetchall()
    return Findings(findings=[FindingOut(**r) for r in rows])


@router.post("/cases/{case_id}/findings/rerun", status_code=202, response_model=Queued)
async def rerun(case_id: uuid.UUID, access: Decide, conn: DbConn) -> Queued:
    return Queued(job_id=await registry.enqueue(conn, case_id, "rerun_checks"))


@router.patch("/cases/{case_id}/findings/{finding_id}", response_model=FindingOut)
async def decide(
    case_id: uuid.UUID, finding_id: uuid.UUID, body: FindingDecision, access: Decide, response: Response, conn: DbConn
) -> FindingOut:
    await _one(conn, case_id, finding_id)
    reason = body.reason.strip() if body.reason else None
    await conn.execute(
        "update app.findings set decision = %s, reason = %s, version = version + 1 where id = %s",
        (body.decision, reason, finding_id),
    )
    await audit.record(conn, action="finding.decided", target=f"finding:{finding_id}", case_id=case_id)
    out = await _one(conn, case_id, finding_id)
    response.headers["etag"] = etag(out.version)
    return out
