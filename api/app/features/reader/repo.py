"""SQL for slice B's tables (`page_reads`, `fact_verifications`, `fact_flags`) and its writes on `pages` and `facts`.

Every function runs on the request's or job's connection, so row-level security applies (ADR 0013).
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection


async def record_read(
    conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID, person_id: uuid.UUID, dwell_seconds: int
) -> None:
    """Append one read to the case's read log (the database numbers, times and hashes it)."""
    await conn.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, %s)",
        (case_id, page_id, person_id, dwell_seconds),
    )


async def has_read(conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> bool:
    cur = await conn.execute(
        "select exists (select 1 from app.page_reads r join app.pages p on p.id = r.page_id"
        " where r.case_id = %s and p.bundle_page_no = %s and r.person_id = %s)",
        (case_id, page_no, person_id),
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def citing_read_page_ids(conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> set[uuid.UUID]:
    cur = await conn.execute(
        "select distinct page_id from app.page_reads where case_id = %s and person_id = %s and counts_for_citing",
        (case_id, person_id),
    )
    return {r[0] for r in await cur.fetchall()}
