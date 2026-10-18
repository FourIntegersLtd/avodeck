"""Facts tied to questions (slice D owns `fact_question_links`; slice E reads them here).

Only links the named expert confirmed count (state-machines.md "Fact-question link"): suggested,
rejected and stale links never do. Runs on the caller's connection, so row-level security applies.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

CONFIRMED = "confirmed"


async def confirmed_links(
    conn: AsyncConnection, case_id: uuid.UUID, question_id: uuid.UUID | None = None
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Question id to the ids of the facts the expert confirmed for it (only questions that have any)."""
    sql = "select question_id, fact_id from app.fact_question_links where case_id = %s and status = %s"
    params: list[object] = [case_id, CONFIRMED]
    if question_id is not None:
        sql += " and question_id = %s"
        params.append(question_id)
    cur = await conn.execute(sql, params)
    links: dict[uuid.UUID, set[uuid.UUID]] = {}
    for question, fact in await cur.fetchall():
        links.setdefault(question, set()).add(fact)
    return links
