"""Facts the named expert has sent on from the reader (slice B owns `fact_flags`).

Slice E reads the facts sent to Points against with `flagged_fact_ids(conn, case_id, "points")`.
Runs on the request's or job's connection, so row-level security applies.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

POINTS = "points"


async def flagged_fact_ids(conn: AsyncConnection, case_id: uuid.UUID, flagged_for: str = POINTS) -> set[uuid.UUID]:
    cur = await conn.execute(
        "select fact_id from app.fact_flags where case_id = %s and flagged_for = %s", (case_id, flagged_for)
    )
    return {r[0] for r in await cur.fetchall()}
