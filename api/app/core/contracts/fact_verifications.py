"""The fact check log (slice B owns `fact_verifications`; slice A writes its re-map rows here).

When a page is read again (turned or typed out), slice A carries each checked fact onto the new words
that still show its value and logs one `remapped` row per fact, with no person (the job did it).
Runs on the job's connection, so row-level security applies.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

REMAPPED = "remapped"


async def log_remaps(
    conn: AsyncConnection, case_id: uuid.UUID, moved: list[tuple[uuid.UUID, str | None, list[str], list[str]]]
) -> None:
    """One `remapped` row per fact (fact, value, old words, new words) a re-read moved onto new words."""
    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value,"
            " old_word_ids, new_word_ids) values (%s, %s, null, %s, %s, %s, %s, %s)",
            [(case_id, f, REMAPPED, value, value, old, new) for f, value, old, new in moved],
        )
