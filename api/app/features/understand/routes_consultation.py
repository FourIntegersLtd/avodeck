"""Screen 12: one consultation as the clinician saw it (architecture 5.2 "12 One consultation").

What had arrived by the day of the visit, what came later (counted, kept apart) and what nobody can
tell ("not known", never placed before the visit). The title and the guidance in force on the day
come from the pack. `?on=` shows the record as it stood on another day. The version of each guideline
shown is recorded for the case; a later change to that pack entry is listed in `guidance_changed`.
The named expert changes when something arrived (`availability.override`, always with a reason,
appended and audited; the latest wins) and adds a note on the visit (`consultation.note`). A note is
its author's own: nobody else sees it, and its author may still change it after the named expert changes.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import Depends, Query, Request
from pydantic import BaseModel, Field, model_validator

from app.core import audit
from app.core.auth.access import CaseAccess, check_case_access, require_case_access
from app.core.http import DbConn
from app.features.understand import guidelines, repo
from app.features.understand.as_seen import SeenItem
from app.features.understand.as_seen import as_seen as seen_on
from app.features.understand.common import case_pack, invalid, not_found
from app.features.understand.guidelines import in_force
from app.features.understand.router import router

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Override = Annotated[CaseAccess, Depends(require_case_access("availability.override"))]


class Entry(BaseModel):
    fact_id: uuid.UUID
    kind: str
    page_no: int
    event_date: date | None
    available_at: date | None
    status: str
    summary: str | None = None
    source: str | None = None
    has_finding: bool = False


class Guidance(BaseModel):
    code: str
    title: str | None
    version_label: str
    url: str | None


class Consultation(BaseModel):
    title: str
    entry: Entry
    visible: list[Entry]
    later_count: int
    later: list[Entry]
    unknown: list[Entry]
    guidance: list[Guidance]
    guidance_changed: list[str] = []
    on: date
    note: str | None
    question_numbers: list[int] = []


def _entry(fact: repo.SeenFact, flagged: set[uuid.UUID] = frozenset()) -> Entry:  # type: ignore[assignment]
    return Entry(
        fact_id=fact.fact_id,
        kind=fact.kind,
        page_no=fact.page_no,
        event_date=fact.event_date,
        available_at=fact.available_at,
        status=fact.status,
        summary=repo.summary_of(fact),
        source=fact.source,
        has_finding=fact.fact_id in flagged,
    )


@router.get("/cases/{case_id}/consultations/{fact_id}", response_model=Consultation)
async def consultation(
    case_id: uuid.UUID,
    fact_id: uuid.UUID,
    access: View,
    request: Request,
    conn: DbConn,
    as_seen: Annotated[bool, Query(description="The basic view always shows the day as seen.")] = True,
    on: Annotated[
        date | None, Query(description="Show the record as it stood on this day (default: the visit).")
    ] = None,
) -> Consultation:
    pack = await case_pack(request, conn, case_id)
    facts = await repo.visible_facts(request, conn, case_id)
    by_id = {f.fact_id: f for f in facts}
    visit = by_id.get(fact_id)
    if visit is None:
        raise not_found()
    on = on or visit.event_date or visit.available_at
    if on is None:
        raise invalid("fact_id", message="This entry has no date.")
    flagged = await repo.flagged_facts(conn, case_id)
    cur = await conn.execute(
        "select distinct q.number from app.fact_question_links l join app.questions q on q.id = l.question_id"
        " where l.case_id = %s and l.fact_id = %s and l.status = 'confirmed' order by q.number",
        (case_id, fact_id),
    )
    numbers = [int(r[0]) for r in await cur.fetchall()]
    view = seen_on(on, (SeenItem(f.fact_id, f.available_at, f.status) for f in facts), exclude=fact_id)

    def pick(items: list[SeenItem]) -> list[Entry]:
        return [_entry(by_id[i.fact_id], flagged) for i in items]

    table = pack.guidelines()
    shown = [r for r in table if in_force([r], str(r["code"]), on)]
    guidance = [
        Guidance(code=str(r["code"]), title=r.get("title"), version_label=str(r["version_label"]), url=r.get("url"))
        for r in shown
    ]
    await guidelines.record_shown(conn, case_id, shown)
    cur = await conn.execute(
        "select body from app.consultation_notes where case_id = %s and fact_id = %s"
        " and person_id = app.current_person_id()",
        (case_id, fact_id),
    )
    note = await cur.fetchone()
    return Consultation(
        title=pack.term("as_seen_title"),
        entry=_entry(visit, flagged),
        visible=pick(view.visible),
        later_count=len(view.later),
        later=pick(view.later),
        unknown=pick(view.unknown),
        guidance=guidance,
        guidance_changed=await guidelines.changed(conn, case_id, table),
        on=on,
        note=note[0] if note else None,
        question_numbers=numbers,
    )


class AvailabilityChange(BaseModel):
    available_at: date | None = None
    unknown: bool = False
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _one(self) -> AvailabilityChange:
        if self.unknown == (self.available_at is not None) or not self.reason.strip():
            raise ValueError("give a date or say it is not known, with a reason")
        return self


class AvailabilityOut(BaseModel):
    fact_id: uuid.UUID
    available_at: date | None
    unknown: bool


@router.patch("/cases/{case_id}/facts/{fact_id}/availability", response_model=AvailabilityOut)
async def change_availability(
    case_id: uuid.UUID, fact_id: uuid.UUID, body: AvailabilityChange, access: Override, conn: DbConn
) -> AvailabilityOut:
    cur = await conn.execute("select 1 from app.facts where id = %s and case_id = %s", (fact_id, case_id))
    if await cur.fetchone() is None:
        raise not_found()
    await conn.execute(
        "insert into app.availability_overrides (case_id, fact_id, available_at, unknown, reason, person_id)"
        " values (%s, %s, %s, %s, %s, app.current_person_id())",
        (case_id, fact_id, body.available_at, body.unknown, body.reason.strip()),
    )
    basis = "expert_unknown" if body.unknown else "expert_override"
    await conn.execute("select app.set_fact_availability(%s, %s, %s)", (fact_id, body.available_at, basis))
    await audit.record(conn, action="fact.availability_changed", target=f"fact:{fact_id}", case_id=case_id)
    return AvailabilityOut(fact_id=fact_id, available_at=body.available_at, unknown=body.unknown)


class NoteIn(BaseModel):
    body: str = Field(max_length=20000)


class NoteOut(BaseModel):
    fact_id: uuid.UUID
    body: str


@router.put("/cases/{case_id}/consultations/{fact_id}/note", response_model=NoteOut)
async def put_note(case_id: uuid.UUID, fact_id: uuid.UUID, body: NoteIn, access: View, conn: DbConn) -> NoteOut:
    """Add a note (`consultation.note`) or change your own; the author keeps theirs if the named expert changes."""
    cur = await conn.execute("select 1 from app.facts where id = %s and case_id = %s", (fact_id, case_id))
    if await cur.fetchone() is None:
        raise not_found()
    cur = await conn.execute(
        "select 1 from app.consultation_notes where case_id = %s and fact_id = %s"
        " and person_id = app.current_person_id()",
        (case_id, fact_id),
    )
    if await cur.fetchone() is None:
        await check_case_access(conn, case_id, "consultation.note")
        await conn.execute(
            "insert into app.consultation_notes (case_id, fact_id, body, person_id)"
            " values (%s, %s, %s, app.current_person_id())",
            (case_id, fact_id, body.body),
        )
    else:
        # An update, never an upsert: the author keeps their note without `consultation.note`.
        await conn.execute(
            "update app.consultation_notes set body = %s where case_id = %s and fact_id = %s"
            " and person_id = app.current_person_id()",
            (body.body, case_id, fact_id),
        )
    await audit.record(conn, action="consultation.noted", target=f"fact:{fact_id}", case_id=case_id)
    return NoteOut(fact_id=fact_id, body=body.body)
