"""Facts the named expert has sent on from the reader (slice B owns `fact_flags`).

Slice E reads the facts sent to Points against with `flagged_fact_ids(conn, case_id, "points")`.
Slice A flags checked facts whose words are gone after a page is read again (turned or typed out)
with `flag_for_recheck`: "Please check" on screen 09, with no person, until the expert checks them.
Runs on the request's or job's connection, so row-level security applies.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

POINTS = "points"
RECHECK = "recheck"


async def flagged_fact_ids(conn: AsyncConnection, case_id: uuid.UUID, flagged_for: str = POINTS) -> set[uuid.UUID]:
    cur = await conn.execute(
        "select fact_id from app.fact_flags where case_id = %s and flagged_for = %s", (case_id, flagged_for)
    )
    return {r[0] for r in await cur.fetchall()}


async def flag_for_recheck(conn: AsyncConnection, case_id: uuid.UUID, fact_ids: list[uuid.UUID]) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, %s, null)"
            " on conflict (fact_id, flagged_for) do nothing",
            [(case_id, f, RECHECK) for f in fact_ids],
        )
