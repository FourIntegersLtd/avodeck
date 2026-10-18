"""Links between checked facts and the questions asked (T-11, architecture 5.2, slice D brief).

Served under `/fact-links`: `/links` already serves linked cases (slice C). Any reader asks for
suggestions (`link.suggest`, a `suggest_links` job); only the named expert confirms, rejects or adds a
link by hand (`link.decide`), only on a checked fact (409 `not_checked`) and a confirmed question
(read through `confirmed_questions`). Every decision is audited. A link whose fact was later rejected,
or whose question was removed (the link is kept, with no question), reads as `stale` and cannot be
confirmed (409 `stale_link`). Each link carries its entry's kind, page and date for review.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, Response
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.questions import confirmed_questions
from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.understand import jobs  # noqa: F401  (registers suggest_links)
from app.features.understand.common import not_found
from app.features.understand.repo import CHECKED
from app.features.understand.router import router

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Suggest = Annotated[CaseAccess, Depends(require_case_access("link.suggest"))]
Decide = Annotated[CaseAccess, Depends(require_case_access("link.decide"))]

LinkStatus = Literal["suggested", "confirmed", "rejected", "stale"]


class LinkOut(BaseModel):
    id: uuid.UUID
    fact_id: uuid.UUID
    question_id: uuid.UUID | None
    source: str
    status: str
    reason: str | None
    version: int
    kind: str
    page_no: int
    event_date: date | None


class Links(BaseModel):
    links: list[LinkOut]


class LinkIn(BaseModel):
    fact_id: uuid.UUID
    question_id: uuid.UUID


class LinkDecision(BaseModel):
    status: Literal["confirmed", "rejected"]


class Queued(BaseModel):
    job_id: uuid.UUID


_L = (
    "select l.id, l.fact_id, l.question_id, l.source,"
    " case when f.status = 'rejected' or l.question_id is null then 'stale' else l.status end as status,"
    " l.reason, l.version, f.kind, p.bundle_page_no as page_no, f.event_date"
    " from app.fact_question_links l join app.facts f on f.id = l.fact_id join app.pages p on p.id = f.page_id"
)


async def _one(conn: DbConn, case_id: uuid.UUID, link_id: uuid.UUID) -> LinkOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{_L} where l.id = %s and l.case_id = %s", (link_id, case_id))
        row = await cur.fetchone()
    if row is None:
        raise not_found()
    return LinkOut(**row)


@router.get("/cases/{case_id}/fact-links", response_model=Links)
async def list_links(case_id: uuid.UUID, access: View, conn: DbConn, status: LinkStatus | None = None) -> Links:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{_L} where l.case_id = %s order by l.created_at, l.id", (case_id,))
        rows = [LinkOut(**r) for r in await cur.fetchall()]
    return Links(links=[r for r in rows if status is None or r.status == status])


@router.post("/cases/{case_id}/fact-links/suggest", status_code=202, response_model=Queued)
async def suggest(case_id: uuid.UUID, access: Suggest, conn: DbConn) -> Queued:
    return Queued(job_id=await registry.enqueue(conn, case_id, "suggest_links"))


async def _fact_checked(conn: DbConn, case_id: uuid.UUID, fact_id: uuid.UUID) -> None:
    cur = await conn.execute("select status from app.facts where id = %s and case_id = %s", (fact_id, case_id))
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    if row[0] not in CHECKED:
        raise ApiError("not_checked", "Check this fact before linking it.", 409)


@router.post("/cases/{case_id}/fact-links", status_code=201, response_model=LinkOut)
async def add_link(case_id: uuid.UUID, body: LinkIn, access: Decide, response: Response, conn: DbConn) -> LinkOut:
    await _fact_checked(conn, case_id, body.fact_id)
    if body.question_id not in {q.id for q in await confirmed_questions(conn, case_id)}:
        raise not_found()
    cur = await conn.execute(
        "insert into app.fact_question_links (case_id, fact_id, question_id, source, status, decided_by, decided_at)"
        " values (%s, %s, %s, 'expert', 'confirmed', app.current_person_id(), now())"
        " on conflict (case_id, fact_id, question_id) do update set status = 'confirmed',"
        " decided_by = app.current_person_id(), decided_at = now(), version = app.fact_question_links.version + 1"
        " returning id",
        (case_id, body.fact_id, body.question_id),
    )
    row = await cur.fetchone()
    assert row is not None
    await audit.record(conn, action="link.added", target=f"fact_question_link:{row[0]}", case_id=case_id)
    out = await _one(conn, case_id, row[0])
    response.headers["etag"] = etag(out.version)
    return out


@router.patch("/cases/{case_id}/fact-links/{link_id}", response_model=LinkOut)
async def decide(
    case_id: uuid.UUID,
    link_id: uuid.UUID,
    body: LinkDecision,
    access: Decide,
    if_match: IfMatch,
    response: Response,
    conn: DbConn,
) -> LinkOut:
    current = await _one(conn, case_id, link_id)
    if current.version != if_match:
        raise stale()
    if body.status == "confirmed":
        if current.status == "stale":
            raise ApiError("stale_link", "This link's entry or question has gone.", 409)
        await _fact_checked(conn, case_id, current.fact_id)
    cur = await conn.execute(
        "update app.fact_question_links set status = %s, decided_by = app.current_person_id(), decided_at = now(),"
        " version = version + 1, updated_at = now() where id = %s and version = %s",
        (body.status, link_id, if_match),
    )
    if cur.rowcount != 1:
        raise stale()
    await audit.record(conn, action=f"link.{body.status}", target=f"fact_question_link:{link_id}", case_id=case_id)
    out = await _one(conn, case_id, link_id)
    response.headers["etag"] = etag(out.version)
    return out
