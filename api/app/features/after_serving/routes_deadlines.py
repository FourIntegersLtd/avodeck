"""Screens 19 and S29: deadlines after serving, court directions and hearings.

The pack's deadlines counted from serving are made once per kind when the report is served (the event
handler) and, for cases served before slice G, the first time anyone reads the list. Every move of a date
is kept in `deadline_changes` with its reason and source (S10); a court direction overrides the pack date.
Dates are court days in the pack's timezone; `due_at` is the end of that day in UTC.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import Response
from pydantic import BaseModel, Field

from app.core import audit
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.packs import DeadlineError, MergedPack
from app.features.after_serving.common import (
    CODE,
    DIRECTION,
    Crypto,
    Pack,
    Setup,
    View,
    conflict,
    due_at,
    fetch_all,
    fetch_one,
    invalid,
    not_found,
    pack_version,
    seal,
    unseal,
)
from app.features.after_serving.router import router

SERVED_FROM = "report_served"
_DEADLINE = (
    "id, kind, due_on, rule_ref, calendar, timezone, created_from, source, pack_version, hearing_id, done_at, cancelled_at,"
    " last_reminded_on, version"
)
_HEARING = "id, type, starts_on, ends_on, venue, status, replaced_by_hearing_id, direction_id, version, changed_at"


# --- models ---------------------------------------------------------------------------------------------


class DeadlineOut(BaseModel):
    id: uuid.UUID
    kind: str
    due_on: date
    due_at: datetime = Field(description="The end of the court day in the court's timezone, in UTC.")
    rule_ref: str | None
    calendar: str | None
    timezone: str
    created_from: str
    source: str = Field(description="Where the current date came from: pack, direction, manual or round.")
    pack_version: str | None
    hearing_id: uuid.UUID | None
    done_at: datetime | None
    cancelled_at: datetime | None = None
    last_reminded_on: date | None
    version: int


class Deadlines(BaseModel):
    served_on: date | None
    deadlines: list[DeadlineOut]


class NewDeadline(BaseModel):
    kind: str = Field(pattern=CODE)
    due_on: date
    hearing_id: uuid.UUID | None = None


class DeadlineChange(BaseModel):
    due_on: date | None = None
    reason: str | None = Field(default=None, pattern=CODE)
    direction_id: uuid.UUID | None = None
    done: bool | None = None


class DeadlineChangeOut(BaseModel):
    id: uuid.UUID
    old_due_on: date | None
    new_due_on: date
    reason: str
    source: str
    source_id: uuid.UUID | None
    changed_by: uuid.UUID | None
    at: datetime


class DeadlineChanges(BaseModel):
    changes: list[DeadlineChangeOut]


class DirectionDeadline(BaseModel):
    kind: str = Field(pattern=CODE)
    due_on: date


class NewDirection(BaseModel):
    made_on: date
    reference: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=20000)
    document_id: uuid.UUID | None = None
    deadlines: list[DirectionDeadline] = Field(default_factory=list, max_length=50)
    stay: bool = False


class DirectionOut(BaseModel):
    id: uuid.UUID
    made_on: date
    reference: str | None
    text: str | None
    document_id: uuid.UUID | None
    stay: bool
    created_deadline_ids: list[uuid.UUID]
    created_at: datetime


class Directions(BaseModel):
    directions: list[DirectionOut]


class NewHearing(BaseModel):
    type: str = Field(pattern=CODE)
    starts_on: date
    ends_on: date
    venue: str | None = Field(default=None, max_length=200)


class HearingChange(BaseModel):
    status: Literal["scheduled", "heard", "vacated", "adjourned"] | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    venue: str | None = Field(default=None, max_length=200)


class HearingOut(BaseModel):
    id: uuid.UUID
    type: str
    starts_on: date
    ends_on: date
    venue: str | None
    status: str
    replaced_by_hearing_id: uuid.UUID | None
    direction_id: uuid.UUID | None
    version: int
    changed_at: datetime | None


class Hearings(BaseModel):
    hearings: list[HearingOut]


# --- deadlines ------------------------------------------------------------------------------------------


def deadline_out(row: dict[str, Any]) -> DeadlineOut:
    return DeadlineOut(**row, due_at=due_at(row["due_on"], row["timezone"]))


async def served_on(conn: Any, case_id: uuid.UUID) -> date | None:
    cur = await conn.execute("select app.after_serving_served_on(%s)", (case_id,))
    row = await cur.fetchone()
    return row[0] if row else None


async def ensure_pack_deadlines(conn: Any, pack: MergedPack, case_id: uuid.UUID) -> date | None:
    """Make the pack's deadlines counted from serving, once per kind; returns the day first served."""
    served = await served_on(conn, case_id)
    if served is None:
        return None
    for rule in pack.deadline_rules():
        if rule.get("from") != SERVED_FROM:
            continue
        try:
            found = pack.deadline(str(rule["key"]), served)
        except DeadlineError:
            continue  # a date the court sets, entered from a direction
        await conn.execute(
            "select app.after_serving_pack_deadline(%s, %s, %s, %s, %s, %s, %s)",
            (case_id, found.kind, found.rule_ref, found.due_on, found.calendar, found.timezone, pack_version(pack)),
        )
    return served


async def _deadline(conn: Any, case_id: uuid.UUID, deadline_id: uuid.UUID) -> dict[str, Any]:
    row = await fetch_one(
        conn, f"select {_DEADLINE} from app.deadlines where id = %s and case_id = %s", (deadline_id, case_id)
    )
    if row is None:
        raise not_found()
    return row


async def add_deadline(
    conn: Any,
    case_id: uuid.UUID,
    *,
    kind: str,
    due_on: date,
    timezone: str,
    created_from: str,
    reason: str,
    source_id: uuid.UUID | None = None,
    hearing_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        "insert into app.deadlines (case_id, kind, due_on, timezone, created_from, source, hearing_id)"
        f" values (%s, %s, %s, %s, %s, %s, %s) returning {_DEADLINE}",
        (case_id, kind, due_on, timezone, created_from, created_from, hearing_id),
    )
    assert row is not None
    await conn.execute(
        "insert into app.deadline_changes (case_id, deadline_id, old_due_on, new_due_on, reason, source, source_id,"
        " changed_by) values (%s, %s, null, %s, %s, %s, %s, app.current_person_id())",
        (case_id, row["id"], due_on, reason, created_from, source_id),
    )
    return row


async def move_deadline(
    conn: Any,
    case_id: uuid.UUID,
    row: dict[str, Any],
    due_on: date,
    *,
    reason: str,
    source: str,
    source_id: uuid.UUID | None = None,
    hearing_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Move a date, keeping the move first (the database refuses a move without one)."""
    await conn.execute(
        "insert into app.deadline_changes (case_id, deadline_id, old_due_on, new_due_on, reason, source, source_id,"
        " changed_by) values (%s, %s, %s, %s, %s, %s, %s, app.current_person_id())",
        (case_id, row["id"], row["due_on"], due_on, reason, source, source_id),
    )
    moved = await fetch_one(
        conn,
        "update app.deadlines set due_on = %s, source = %s, hearing_id = coalesce(%s, hearing_id)"
        f" where id = %s returning {_DEADLINE}",
        (due_on, source, hearing_id, row["id"]),
    )
    assert moved is not None
    return moved


@router.get("/cases/{case_id}/deadlines", response_model=Deadlines)
async def list_deadlines(case_id: uuid.UUID, access: View, conn: DbConn, pack: Pack) -> Deadlines:
    served = await ensure_pack_deadlines(conn, pack, case_id)
    if served is None:
        return Deadlines(served_on=None, deadlines=[])
    rows = await fetch_all(
        conn, f"select {_DEADLINE} from app.deadlines where case_id = %s order by due_on, created_at, id", (case_id,)
    )
    return Deadlines(served_on=served, deadlines=[deadline_out(r) for r in rows])


async def _hearing_in_case(conn: Any, case_id: uuid.UUID, hearing_id: uuid.UUID | None) -> None:
    if hearing_id is None:
        return
    if (
        await fetch_one(conn, "select 1 from app.hearings where id = %s and case_id = %s", (hearing_id, case_id))
        is None
    ):
        raise invalid("hearing_id")


@router.post("/cases/{case_id}/deadlines", status_code=201, response_model=DeadlineOut)
async def create_deadline(
    case_id: uuid.UUID, body: NewDeadline, access: Setup, conn: DbConn, pack: Pack, response: Response
) -> DeadlineOut:
    await _hearing_in_case(conn, case_id, body.hearing_id)
    row = await add_deadline(
        conn,
        case_id,
        kind=body.kind,
        due_on=body.due_on,
        timezone=pack.timezone,
        created_from="manual",
        reason="added",
        hearing_id=body.hearing_id,
    )
    response.headers["etag"] = etag(row["version"])
    return deadline_out(row)


@router.patch("/cases/{case_id}/deadlines/{deadline_id}", response_model=DeadlineOut)
async def change_deadline(
    case_id: uuid.UUID,
    deadline_id: uuid.UUID,
    body: DeadlineChange,
    access: Setup,
    if_match: IfMatch,
    conn: DbConn,
    response: Response,
) -> DeadlineOut:
    row = await _deadline(conn, case_id, deadline_id)
    if row["version"] != if_match:
        raise stale()
    if body.due_on is not None and body.due_on != row["due_on"]:
        source, source_id = "manual", None
        if body.direction_id is not None:
            found = await fetch_one(
                conn, "select 1 from app.court_directions where id = %s and case_id = %s", (body.direction_id, case_id)
            )
            if found is None:
                raise invalid("direction_id")
            source, source_id = "direction", body.direction_id
        row = await move_deadline(
            conn, case_id, row, body.due_on, reason=body.reason or "moved", source=source, source_id=source_id
        )
    if body.done is not None:
        updated = await fetch_one(
            conn,
            "update app.deadlines set done_at = case when %s then coalesce(done_at, now()) end"
            f" where id = %s returning {_DEADLINE}",
            (body.done, deadline_id),
        )
        assert updated is not None
        row = updated
    response.headers["etag"] = etag(row["version"])
    return deadline_out(row)


@router.get("/cases/{case_id}/deadlines/{deadline_id}/changes", response_model=DeadlineChanges)
async def deadline_changes(case_id: uuid.UUID, deadline_id: uuid.UUID, access: View, conn: DbConn) -> DeadlineChanges:
    await _deadline(conn, case_id, deadline_id)
    rows = await fetch_all(
        conn,
        "select id, old_due_on, new_due_on, reason, source, source_id, changed_by, at from app.deadline_changes"
        " where deadline_id = %s and case_id = %s order by at, old_due_on nulls first, id",
        (deadline_id, case_id),
    )
    return DeadlineChanges(changes=[DeadlineChangeOut(**r) for r in rows])


# --- court directions -----------------------------------------------------------------------------------


@router.post("/cases/{case_id}/court-directions", status_code=201, response_model=DirectionOut)
async def add_direction(
    case_id: uuid.UUID, body: NewDirection, access: Setup, conn: DbConn, crypto: Crypto, pack: Pack
) -> DirectionOut:
    if body.document_id is not None:
        found = await fetch_one(
            conn, "select 1 from app.documents where id = %s and case_id = %s", (body.document_id, case_id)
        )
        if found is None:
            raise invalid("document_id")
    await ensure_pack_deadlines(conn, pack, case_id)
    direction_id = uuid.uuid4()
    created: list[uuid.UUID] = []
    for item in body.deadlines:
        existing = await fetch_one(
            conn,
            f"select {_DEADLINE} from app.deadlines where case_id = %s and kind = %s and done_at is null"
            " order by (created_from = 'pack') desc, created_at desc limit 1",
            (case_id, item.kind),
        )
        if existing is None:
            made = await add_deadline(
                conn,
                case_id,
                kind=item.kind,
                due_on=item.due_on,
                timezone=pack.timezone,
                created_from="direction",
                reason="court_direction",
                source_id=direction_id,
            )
            created.append(made["id"])
        elif existing["due_on"] != item.due_on:
            await move_deadline(
                conn,
                case_id,
                existing,
                item.due_on,
                reason="court_direction",
                source="direction",
                source_id=direction_id,
            )
    text = await seal(conn, crypto, case_id, body.text, DIRECTION) if body.text else None
    row = await fetch_one(
        conn,
        "insert into app.court_directions (id, case_id, made_on, reference, text, document_id, stay,"
        " created_deadline_ids, created_by) values (%s, %s, %s, %s, %s, %s, %s, %s, app.current_person_id())"
        " returning id, made_on, reference, document_id, stay, created_deadline_ids, created_at",
        (direction_id, case_id, body.made_on, body.reference, text, body.document_id, body.stay, created),
    )
    assert row is not None
    if body.stay:
        held = await fetch_one(
            conn, "update app.cases set on_hold = true where id = %s and not on_hold returning id", (case_id,)
        )
        if held is not None:
            await audit.record(conn, action="case.held", target=f"case:{case_id}", case_id=case_id)
    await audit.record(conn, action="court_direction.added", target=f"court_direction:{direction_id}", case_id=case_id)
    return DirectionOut(**row, text=body.text)


@router.get("/cases/{case_id}/court-directions", response_model=Directions)
async def list_directions(case_id: uuid.UUID, access: View, conn: DbConn, crypto: Crypto) -> Directions:
    rows = await fetch_all(
        conn,
        "select id, made_on, reference, text, document_id, stay, created_deadline_ids, created_at"
        " from app.court_directions where case_id = %s order by made_on, created_at, id",
        (case_id,),
    )
    out = []
    for r in rows:
        r["text"] = await unseal(conn, crypto, case_id, r["text"], DIRECTION)
        out.append(DirectionOut(**r))
    return Directions(directions=out)


# --- hearings -------------------------------------------------------------------------------------------


async def _insert_hearing(
    conn: Any, case_id: uuid.UUID, type_: str, starts: date, ends: date, venue: str | None
) -> dict[str, Any]:
    if ends < starts:
        raise invalid("ends_on")
    row = await fetch_one(
        conn,
        f"insert into app.hearings (case_id, type, starts_on, ends_on, venue) values (%s, %s, %s, %s, %s)"
        f" returning {_HEARING}",
        (case_id, type_, starts, ends, venue),
    )
    assert row is not None
    return row


@router.post("/cases/{case_id}/hearings", status_code=201, response_model=HearingOut)
async def add_hearing(
    case_id: uuid.UUID, body: NewHearing, access: Setup, conn: DbConn, response: Response
) -> HearingOut:
    row = await _insert_hearing(conn, case_id, body.type, body.starts_on, body.ends_on, body.venue)
    response.headers["etag"] = etag(row["version"])
    return HearingOut(**row)


@router.patch("/cases/{case_id}/hearings/{hearing_id}", response_model=HearingOut)
async def change_hearing(
    case_id: uuid.UUID,
    hearing_id: uuid.UUID,
    body: HearingChange,
    access: Setup,
    if_match: IfMatch,
    conn: DbConn,
    response: Response,
) -> HearingOut:
    old = await fetch_one(
        conn, f"select {_HEARING} from app.hearings where id = %s and case_id = %s", (hearing_id, case_id)
    )
    if old is None:
        raise not_found()
    if old["version"] != if_match:
        raise stale()
    if old["status"] != "scheduled":
        raise conflict("conflict", "A finished hearing does not change.")
    if body.status == "adjourned":
        if body.starts_on is None or body.ends_on is None:
            raise invalid("starts_on", "ends_on")
        new = await _insert_hearing(
            conn, case_id, old["type"], body.starts_on, body.ends_on, body.venue or old["venue"]
        )
        row = await fetch_one(
            conn,
            f"update app.hearings set status = 'adjourned', replaced_by_hearing_id = %s where id = %s returning"
            f" {_HEARING}",
            (new["id"], hearing_id),
        )
        shift = body.starts_on - old["starts_on"]
        tied = await fetch_all(
            conn,
            f"select {_DEADLINE} from app.deadlines where case_id = %s and hearing_id = %s and done_at is null",
            (case_id, hearing_id),
        )
        for deadline in tied:
            await move_deadline(
                conn,
                case_id,
                deadline,
                deadline["due_on"] + shift,
                reason="adjourned",
                source="manual",
                source_id=new["id"],
                hearing_id=new["id"],
            )
    else:
        starts = body.starts_on or old["starts_on"]
        ends = body.ends_on or old["ends_on"]
        if ends < starts:
            raise invalid("ends_on")
        row = await fetch_one(
            conn,
            "update app.hearings set status = coalesce(%s, status), starts_on = %s, ends_on = %s,"
            f" venue = coalesce(%s, venue) where id = %s returning {_HEARING}",
            (body.status, starts, ends, body.venue, hearing_id),
        )
    assert row is not None
    await audit.record(conn, action="hearing.changed", target=f"hearing:{hearing_id}", case_id=case_id)
    response.headers["etag"] = etag(row["version"])
    return HearingOut(**row)


@router.get("/cases/{case_id}/hearings", response_model=Hearings)
async def list_hearings(case_id: uuid.UUID, access: View, conn: DbConn) -> Hearings:
    rows = await fetch_all(
        conn, f"select {_HEARING} from app.hearings where case_id = %s order by starts_on, created_at, id", (case_id,)
    )
    return Hearings(hearings=[HearingOut(**r) for r in rows])
