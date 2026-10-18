"""Facts tied to questions (slice D owns `fact_question_links`; slice E reads them here).

Only links the named expert confirmed count (state-machines.md "Fact-question link"): suggested,
rejected and stale links never do, nor a confirmed link whose fact is no longer checked (verified or
edited), or is flagged "Please check" (as in citable_facts): slice D cannot see slice B's fact
changes, so such a link stays 'confirmed' in the table.
Runs on the caller's connection, so row-level security applies.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core.contracts.fact_flags import RECHECK
from app.core.contracts.facts import CITABLE_STATUSES

CONFIRMED = "confirmed"


async def confirmed_links(
    conn: AsyncConnection, case_id: uuid.UUID, question_id: uuid.UUID | None = None
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Question id to the ids of the facts the expert confirmed for it (only questions that have any)."""
    sql = (
        "select l.question_id, l.fact_id from app.fact_question_links l"
        " join app.facts f on f.id = l.fact_id"
        " where l.case_id = %s and l.status = %s and f.status = any(%s)"
        " and not exists (select 1 from app.fact_flags g where g.fact_id = f.id and g.flagged_for = %s)"
    )
    params: list[object] = [case_id, CONFIRMED, sorted(CITABLE_STATUSES), RECHECK]
    if question_id is not None:
        sql += " and l.question_id = %s"
        params.append(question_id)
    cur = await conn.execute(sql, params)
    links: dict[uuid.UUID, set[uuid.UUID]] = {}
    for question, fact in await cur.fetchall():
        links.setdefault(question, set()).add(fact)
    return links
