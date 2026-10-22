"""Answers slice D's question "where does this record source go quiet?" from `app.record_gaps`
(contract: `app.core.contracts.record_gaps`). A gap the expert rejected (`confirmed = false`) does not count."""

from __future__ import annotations

import uuid
from datetime import date

from psycopg import AsyncConnection

from app.core.contracts import record_gaps


@record_gaps.register_reader
async def read(conn: AsyncConnection, case_id: uuid.UUID, start: date, end: date) -> list[record_gaps.RecordGap]:
    cur = await conn.execute(
        "select source_type, fact_kind, gap_start, gap_end from app.record_gaps"
        " where case_id = %s and gap_start <= %s and gap_end >= %s and confirmed is not false",
        (case_id, end, start),
    )
    return [record_gaps.RecordGap(r[0], r[1], r[2], r[3]) for r in await cur.fetchall()]
