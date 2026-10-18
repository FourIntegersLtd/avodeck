"""Screen 11: the timeline (architecture 5.2 "11 Timeline", slice D brief).

The lanes, their order and their labels come from the case's pack (`pack.lanes()`,
`vocabulary.lanes`). Each event carries its status and the questions it is linked to. Filters: date
range, status and linked question. Suggestions on pages the person has not read stay hidden (rule 3).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, Query, Request
from pydantic import BaseModel

from app.core.auth.access import CaseAccess, require_case_access
from app.core.http import DbConn
from app.features.understand import guidelines, repo
from app.features.understand.common import case_pack
from app.features.understand.lanes import arrange
from app.features.understand.router import router

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Status = Literal["suggested", "verified", "edited"]


class Event(BaseModel):
    fact_id: uuid.UUID
    kind: str
    page_no: int
    event_date: date | None
    available_at: date | None
    status: str
    question_ids: list[uuid.UUID]
    has_finding: bool


class Lane(BaseModel):
    key: str
    label: str
    events: list[Event]


class Timeline(BaseModel):
    lanes: list[Lane]
    unplaced: list[Event]
    guidance_changed: list[str] = []


@router.get("/cases/{case_id}/timeline", response_model=Timeline)
async def timeline(
    case_id: uuid.UUID,
    access: View,
    request: Request,
    conn: DbConn,
    since: Annotated[date | None, Query(alias="from")] = None,
    until: Annotated[date | None, Query(alias="to")] = None,
    status: Status | None = None,
    question_id: uuid.UUID | None = None,
) -> Timeline:
    pack = await case_pack(request, conn, case_id)
    facts = await repo.visible_facts(request, conn, case_id)
    links = await repo.linked_questions(conn, case_id)
    cur = await conn.execute(
        "select distinct unnest(evidence_fact_ids) from app.findings where case_id = %s and status = 'open'",
        (case_id,),
    )
    flagged = {r[0] for r in await cur.fetchall()}

    def keep(fact: repo.SeenFact) -> bool:
        if since is not None and (fact.event_date is None or fact.event_date < since):
            return False
        if until is not None and (fact.event_date is None or fact.event_date > until):
            return False
        if status == "verified" and fact.status not in repo.CHECKED:
            return False
        if status not in (None, "verified") and fact.status != status:
            return False
        return question_id is None or question_id in links.get(fact.fact_id, [])

    def event(fact: repo.SeenFact) -> Event:
        return Event(
            fact_id=fact.fact_id,
            kind=fact.kind,
            page_no=fact.page_no,
            event_date=fact.event_date,
            available_at=fact.available_at,
            status=fact.status,
            question_ids=links.get(fact.fact_id, []),
            has_finding=fact.fact_id in flagged,
        )

    lanes = pack.lanes()
    placed, unplaced = arrange([f for f in facts if keep(f)], lanes, kind=lambda f: f.kind, fields=lambda f: f.fields)
    labels = pack.vocabulary.get("lanes", {})
    return Timeline(
        lanes=[Lane(key=key, label=str(labels.get(key, key)), events=[event(f) for f in got]) for key, got in placed],
        unplaced=[event(f) for f in unplaced],
        guidance_changed=await guidelines.changed(conn, case_id, pack.guidelines()),
    )
