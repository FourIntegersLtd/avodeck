"""Screen 14: points for and against one question (architecture 5.2, slice E brief).

A point is a verified fact placed on a side; the sides' codes are the core's and their labels the case
type's (`pack.point_sides()`). Facts the expert flagged on screen 09 come first, then points by side.
The named expert moves a point, accepts it or removes it with a reason (If-Match on the point's
version), or adds an unused citable fact as a point; each is audited. Citable facts the draft does not
cite that T-14b picked show as relevant facts until the draft cites them; the expert can dismiss one.
The database keeps the allowed status changes (migration 0500) and marks a point stale when its fact changes (0504).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import psycopg
from fastapi import Request, Response
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.contracts.facts import Fact
from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.write import services
from app.features.write.common import (
    Crypto,
    PointFact,
    QuestionHeader,
    Queued,
    View,
    Write,
    case_pack,
    citable,
    invalid,
    not_found,
    question_header,
    refused,
    require_question,
)
from app.features.write.router import router
from app.features.write.routes_range_note import RangeNote, range_note
from app.features.write.routes_witness import ClaimOut, open_claims

_POINT = (
    "select p.*, f.page_id, f.kind, f.value, f.event_date, pg.bundle_page_no as page_no from app.points p"
    " join app.facts f on f.id = p.fact_id join app.pages pg on pg.id = f.page_id"
)


class Side(BaseModel):
    code: str
    label: str


class PointOut(BaseModel):
    id: uuid.UUID
    side: str
    status: str
    origin: str
    reason: str | None
    dismissed_reason: str | None
    flagged: bool
    version: int
    fact: PointFact


class RelevantOut(BaseModel):
    id: uuid.UUID
    status: str
    why_relevant: str
    version: int
    fact: PointFact


class Points(BaseModel):
    sides: list[Side]
    points: list[PointOut]
    unused: list[PointFact]
    relevant: list[RelevantOut]
    claims: list[ClaimOut]
    range_note: RangeNote
    question: QuestionHeader


class PointChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: str | None = Field(default=None, max_length=40)
    status: Literal["accepted", "dismissed"] | None = None
    dismissed_reason: str | None = Field(default=None, min_length=1, max_length=200)


class RelevantChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["dismissed"]


class PointAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: uuid.UUID
    side: str = Field(max_length=40)


def _point(row: dict[str, Any], flagged: set[uuid.UUID]) -> PointOut:
    fact = PointFact(id=row["fact_id"], **{k: row[k] for k in ("page_id", "page_no", "kind", "value", "event_date")})
    keep = ("id", "side", "status", "origin", "reason", "dismissed_reason", "version")
    return PointOut(**{k: row[k] for k in keep}, flagged=row["fact_id"] in flagged, fact=fact)


async def _rows(conn: DbConn, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, args)
        return await cur.fetchall()


async def _page_numbers(conn: DbConn, page_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    cur = await conn.execute("select id, bundle_page_no from app.pages where id = any(%s)", (list(page_ids),))
    return {r[0]: r[1] for r in await cur.fetchall()}


async def _linked(request: Request, conn: DbConn, case_id: uuid.UUID, question_id: uuid.UUID) -> list[Fact]:
    return await services.current().links.linked(conn, case_id, question_id, await citable(request, conn, case_id))


def _check_side(side: str, sides: list[dict[str, str]]) -> None:
    if side not in {s["code"] for s in sides}:
        raise invalid("side")


@router.get("/cases/{case_id}/questions/{question_id}/points", response_model=Points)
async def list_points(
    case_id: uuid.UUID, question_id: uuid.UUID, request: Request, access: View, conn: DbConn, crypto: Crypto
) -> Points:
    """The points on one question, flagged facts first, then by side; and the citable facts not yet used."""
    await require_question(conn, case_id, question_id)
    pack = await case_pack(request, conn, case_id)
    sides = pack.point_sides()
    order = {s["code"]: i for i, s in enumerate(sides)}
    flagged = await services.current().flags.flagged_for_points(conn, case_id)
    rows = await _rows(
        conn,
        _POINT + " where p.case_id = %s and p.question_id = %s order by p.created_at, p.id",
        (case_id, question_id),
    )
    points = sorted((_point(r, flagged) for r in rows), key=lambda p: (not p.flagged, order.get(p.side, len(order))))
    used = {p.fact.id for p in points}
    spare = [f for f in await _linked(request, conn, case_id, question_id) if f.id not in used]
    pages = await _page_numbers(conn, {f.page_id for f in spare})
    unused = [
        PointFact(
            id=f.id, page_id=f.page_id, page_no=pages[f.page_id], kind=f.kind, value=f.value, event_date=f.event_date
        )
        for f in sorted(spare, key=lambda f: (f.id not in flagged, pages[f.page_id]))
    ]
    note = await range_note(conn, crypto, case_id, question_id, pack.range_note_prompt())
    relevant = await _relevant(conn, case_id, question_id, {f.id: f for f in unused})
    claims = await open_claims(conn, crypto, case_id)
    return Points(
        sides=[Side(**s) for s in sides],
        points=points,
        unused=unused,
        relevant=relevant,
        claims=claims,
        range_note=note,
        question=await question_header(conn, pack, case_id, question_id),
    )


async def _relevant(
    conn: DbConn, case_id: uuid.UUID, question_id: uuid.UUID, unused: dict[uuid.UUID, PointFact]
) -> list[RelevantOut]:
    """T-14b's suggestions still open: the fact still citable, not a point and not cited by the draft."""
    cur = await conn.execute("select citation_refs from app.opinions where question_id = %s", (question_id,))
    opinion = await cur.fetchone()
    cited = {str(c["fact_id"]) for c in opinion[0]} if opinion else set()
    rows = await _rows(
        conn,
        "select * from app.relevant_facts where case_id = %s and question_id = %s and status = 'suggested'"
        " order by created_at, id",
        (case_id, question_id),
    )
    return [
        RelevantOut(**{k: r[k] for k in ("id", "status", "why_relevant", "version")}, fact=unused[r["fact_id"]])
        for r in rows
        if r["fact_id"] in unused and str(r["fact_id"]) not in cited
    ]


@router.post("/cases/{case_id}/questions/{question_id}/points", response_model=PointOut, status_code=201)
async def add_point(
    case_id: uuid.UUID,
    question_id: uuid.UUID,
    adding: PointAdd,
    request: Request,
    response: Response,
    access: Write,
    conn: DbConn,
) -> PointOut:
    """The expert adds a citable fact linked to the question as a point of their own."""
    await require_question(conn, case_id, question_id)
    _check_side(adding.side, (await case_pack(request, conn, case_id)).point_sides())
    fact = next((f for f in await _linked(request, conn, case_id, question_id) if f.id == adding.fact_id), None)
    if fact is None:
        raise invalid("fact_id")
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "insert into app.points (case_id, question_id, fact_id, side, status, origin, fact_version)"
                " values (%s, %s, %s, %s, 'accepted', 'expert', %s) returning id",
                (case_id, question_id, fact.id, adding.side, fact.version),
            )
    except UniqueViolation:
        raise ApiError("conflict", "This fact is already a point on this question.", 409) from None
    row = await cur.fetchone()
    assert row is not None
    await audit.record(conn, action="point.added", target=f"point:{row[0]}", case_id=case_id)
    out = (await _rows(conn, _POINT + " where p.id = %s", (row[0],)))[0]
    response.headers["ETag"] = etag(out["version"])
    return _point(out, await services.current().flags.flagged_for_points(conn, case_id))


@router.post("/cases/{case_id}/questions/{question_id}/points/suggest", response_model=Queued, status_code=202)
async def suggest(case_id: uuid.UUID, question_id: uuid.UUID, access: Write, conn: DbConn) -> Queued:
    """Ask for the facts not yet points to be sorted by side (T-14a); the expert decides each one."""
    await require_question(conn, case_id, question_id)
    return Queued(job_id=await registry.enqueue(conn, case_id, "suggest_points"))


@router.patch("/cases/{case_id}/points/{point_id}", response_model=PointOut)
async def change_point(
    case_id: uuid.UUID,
    point_id: uuid.UUID,
    change: PointChange,
    if_match: IfMatch,
    request: Request,
    response: Response,
    access: Write,
    conn: DbConn,
) -> PointOut:
    """Move a point to another side, accept it, or remove it with a reason."""
    found = await _rows(conn, "select * from app.points where case_id = %s and id = %s for update", (case_id, point_id))
    if not found:
        raise not_found()
    point = found[0]
    if point["version"] != if_match:
        raise stale()
    if change.side is not None:
        _check_side(change.side, (await case_pack(request, conn, case_id)).point_sides())
    if change.status == "dismissed" and change.dismissed_reason is None:
        raise invalid("dismissed_reason")
    actions = []
    if change.side is not None and change.side != point["side"]:
        actions.append("point.moved")
    if change.status is not None:
        actions.append(f"point.{change.status}")
    try:
        async with conn.transaction():
            await conn.execute(
                "update app.points set side = coalesce(%s, side), status = coalesce(%s, status),"
                " dismissed_reason = case when %s::text is null then dismissed_reason else %s end where id = %s",
                (change.side, change.status, change.dismissed_reason, change.dismissed_reason, point_id),
            )
    except psycopg.Error as exc:
        raise refused(exc) from None
    for action in actions:
        await audit.record(conn, action=action, target=f"point:{point_id}", case_id=case_id)
    out = (await _rows(conn, _POINT + " where p.id = %s", (point_id,)))[0]
    response.headers["ETag"] = etag(out["version"])
    return _point(out, await services.current().flags.flagged_for_points(conn, case_id))


@router.patch("/cases/{case_id}/relevant-facts/{relevant_id}", response_model=RelevantOut)
async def dismiss_relevant(
    case_id: uuid.UUID,
    relevant_id: uuid.UUID,
    change: RelevantChange,
    if_match: IfMatch,
    request: Request,
    response: Response,
    access: Write,
    conn: DbConn,
) -> RelevantOut:
    """Dismiss a suggested relevant fact; the expert uses one by citing it in the draft."""
    found = await _rows(
        conn, "select * from app.relevant_facts where case_id = %s and id = %s for update", (case_id, relevant_id)
    )
    if not found:
        raise not_found()
    if found[0]["version"] != if_match:
        raise stale()
    try:
        async with conn.transaction():
            await conn.execute("update app.relevant_facts set status = %s where id = %s", (change.status, relevant_id))
    except psycopg.Error as exc:
        raise refused(exc) from None
    await audit.record(conn, action="relevant_fact.dismissed", target=f"relevant_fact:{relevant_id}", case_id=case_id)
    row = (
        await _rows(
            conn,
            "select r.*, f.page_id, f.kind, f.value, f.event_date, pg.bundle_page_no as page_no"
            " from app.relevant_facts r join app.facts f on f.id = r.fact_id join app.pages pg on pg.id = f.page_id"
            " where r.id = %s",
            (relevant_id,),
        )
    )[0]
    response.headers["ETag"] = etag(row["version"])
    fact = PointFact(id=row["fact_id"], **{k: row[k] for k in ("page_id", "page_no", "kind", "value", "event_date")})
    return RelevantOut(**{k: row[k] for k in ("id", "status", "why_relevant", "version")}, fact=fact)
