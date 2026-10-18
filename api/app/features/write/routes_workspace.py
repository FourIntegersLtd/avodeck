"""Screen 15's workspace: everything the expert writes beside, for one question (architecture 5.2).

Chips are the citable facts linked to the question (slice D's links behind a port), each with the words a
citation quotes, so the browser never builds citation text (rule 2). Guidance comes from slice D's table
behind a port, for `?on=` or else the latest event date among the chips. The points summary counts the
points not dismissed on each side, with the pack's labels, and the accounts that differ. The views and
the legal test text come from the packs (rule 11).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import Request
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core.http import DbConn
from app.features.write import services
from app.features.write.common import Crypto, View, case_pack, citable, require_question
from app.features.write.lint_store import open_issues
from app.features.write.ports import WorkspaceGuidance
from app.features.write.router import router
from app.features.write.routes_opinion import OpinionOut, opinion_out
from app.features.write.routes_witness import open_claims


class Chip(BaseModel):
    fact_id: uuid.UUID
    page_id: uuid.UUID
    page_no: int
    kind: str
    value: str
    event_date: date | None
    quote_verbatim: str


class SideCount(BaseModel):
    code: str
    label: str
    count: int


class PointsSummary(BaseModel):
    sides: list[SideCount]
    differ: int


class OpinionView(BaseModel):
    code: str
    label: str


class Workspace(BaseModel):
    question_id: uuid.UUID
    chips: list[Chip]
    guidance_on: date | None
    guidance: list[WorkspaceGuidance]
    points: PointsSummary
    views: list[OpinionView]
    legal_test: str
    opinion: OpinionOut | None


@router.get("/cases/{case_id}/questions/{question_id}/workspace", response_model=Workspace)
async def workspace(
    case_id: uuid.UUID,
    question_id: uuid.UUID,
    request: Request,
    access: View,
    conn: DbConn,
    crypto: Crypto,
    on: date | None = None,
) -> Workspace:
    """The chips, guidance on the day, points summary, views, legal test and saved opinion for one question."""
    await require_question(conn, case_id, question_id)
    pack = await case_pack(request, conn, case_id)
    current = services.current()
    linked = await current.links.linked(conn, case_id, question_id, await citable(request, conn, case_id))
    cur = await conn.execute(
        "select id, bundle_page_no from app.pages where id = any(%s)", (list({f.page_id for f in linked}),)
    )
    pages = {r[0]: r[1] for r in await cur.fetchall()}
    chips = [
        Chip(
            fact_id=f.id,
            page_id=f.page_id,
            page_no=pages[f.page_id],
            kind=f.kind,
            value=f.value,
            event_date=f.event_date,
            quote_verbatim=f.value,
        )
        for f in sorted(linked, key=lambda f: (pages[f.page_id], f.event_date or date.min, str(f.id)))
        if f.value
    ]
    if on is None:
        on = max((c.event_date for c in chips if c.event_date), default=None)
    guidance = [] if on is None else await current.guidance.in_force(conn, case_id, pack, on)
    return Workspace(
        question_id=question_id,
        chips=chips,
        guidance_on=on,
        guidance=guidance,
        points=await _summary(conn, crypto, case_id, question_id, pack.point_sides()),
        views=[OpinionView(**v) for v in pack.opinion_views()],
        legal_test=pack.legal_tests(),
        opinion=await _opinion(conn, crypto, case_id, question_id),
    )


async def _summary(
    conn: DbConn, crypto: Crypto, case_id: uuid.UUID, question_id: uuid.UUID, sides: list[dict[str, str]]
) -> PointsSummary:
    cur = await conn.execute(
        "select side, count(*) from app.points where case_id = %s and question_id = %s and status <> 'dismissed'"
        " group by side",
        (case_id, question_id),
    )
    counts: dict[str, int] = {r[0]: r[1] for r in await cur.fetchall()}
    differ = sum(
        1 for c in await open_claims(conn, crypto, case_id) if (c.expert_verdict or c.verdict) == "contradicts"
    )
    return PointsSummary(sides=[SideCount(**s, count=counts.get(s["code"], 0)) for s in sides], differ=differ)


async def _opinion(conn: DbConn, crypto: Crypto, case_id: uuid.UUID, question_id: uuid.UUID) -> OpinionOut | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from app.opinions where case_id = %s and question_id = %s", (case_id, question_id))
        row = await cur.fetchone()
    if row is None:
        return None
    out = await opinion_out(conn, crypto, row)
    out.lint = await open_issues(conn, crypto, case_id, row["id"])
    return out
