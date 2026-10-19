"""The trial slice's experts' discussions and latest trial pack, read-only (request from slice G, screen 19).

Slice I writes `expert_meetings` and `trial_packs`; the after-serving timeline only needs when each
discussion is, how far it has got, and whether the newest trial pack is current: stale when slice I marked
it so, or when what it was built from is out of date now (`trial_freshness`: an adjourned or changed
hearing, new pages, a newer served version, a re-issued court bundle). Runs on the caller's connection, so
row-level security applies (`case.view_content`). Dates and statuses only: no names, no text.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from psycopg import AsyncConnection

from app.core.contracts import trial_freshness


@dataclass(frozen=True)
class MeetingStep:
    id: uuid.UUID
    type: str
    discussion_on: date
    sign_by: date
    status: str


@dataclass(frozen=True)
class PackStep:
    built_on: date
    stale: bool


async def meeting_steps(conn: AsyncConnection, case_id: uuid.UUID) -> tuple[MeetingStep, ...]:
    cur = await conn.execute(
        "select id, type, discussion_on, sign_by, status from app.expert_meetings where case_id = %s"
        " order by discussion_on, created_at",
        (case_id,),
    )
    return tuple(MeetingStep(*row) for row in await cur.fetchall())


async def latest_pack_step(conn: AsyncConnection, case_id: uuid.UUID) -> PackStep | None:
    cur = await conn.execute(
        "select p.created_at::date, p.stale, to_jsonb(p) from app.trial_packs p where p.case_id = %s"
        " order by p.created_at desc limit 1",
        (case_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    built_on, stale, data = row
    if not stale:
        stale = await trial_freshness.stale_reason(conn, case_id, trial_freshness.built_from_row(data)) is not None
    return PackStep(built_on, bool(stale))
