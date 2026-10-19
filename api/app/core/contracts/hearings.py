"""Hearings, read-only (slice G writes `hearings`; slice I binds trial packs and offline copies to one).

A hearing moves scheduled -> heard / vacated / adjourned; an adjourned hearing names the hearing that
replaces it, and every change raises `version` and sets `changed_at` (migration 0700). Statuses, versions
and times only: no venue, no text. Runs on the caller's connection, so row-level security applies; `None`
when the hearing is not the case's or the person cannot see it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection


@dataclass(frozen=True)
class HearingState:
    id: uuid.UUID
    status: str
    version: int
    replaced_by_hearing_id: uuid.UUID | None
    changed_at: datetime | None


async def hearing_state(conn: AsyncConnection, case_id: uuid.UUID, hearing_id: uuid.UUID) -> HearingState | None:
    cur = await conn.execute(
        "select id, status, version, replaced_by_hearing_id, changed_at from app.hearings"
        " where id = %s and case_id = %s",
        (hearing_id, case_id),
    )
    row = await cur.fetchone()
    return HearingState(row[0], str(row[1]), int(row[2]), row[3], row[4]) if row is not None else None
