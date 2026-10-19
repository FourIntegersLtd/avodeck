"""The trial slice's experts' discussions and latest trial pack, read-only (request from slice G, screen 19).

Slice I writes `expert_meetings` and `trial_packs`; the after-serving timeline only needs when each
discussion is, how far it has got, and whether the newest trial pack is current. Runs on the caller's
connection, so row-level security applies (`case.view_content`). Dates and statuses only: no names, no text.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from psycopg import AsyncConnection


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
        "select created_at::date, stale from app.trial_packs where case_id = %s order by created_at desc limit 1",
        (case_id,),
    )
    row = await cur.fetchone()
    return PackStep(*row) if row is not None else None
