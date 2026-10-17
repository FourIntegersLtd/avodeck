"""Facts (architecture 4, rule 2): a value read from a numbered page, citing the OCR words it came from.

Slice A suggests them; slice B's expert verifies them; slices D, E and F read them. Only
`citable_facts()` may be cited in a report: verified (or edited, which counts as verified) facts
on pages the named expert has read (state-machines.md "Fact").
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.core.contracts.reading import PageReadLog

FactStatus = Literal["suggested", "verified", "edited", "rejected"]
CITABLE_STATUSES: frozenset[str] = frozenset({"verified", "edited"})


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    case_id: uuid.UUID
    page_id: uuid.UUID
    kind: str
    fields: dict[str, Any] = Field(default_factory=dict)
    value: str | None = None
    word_ids: list[str] = Field(min_length=1)
    bbox: Any = None
    event_date: date | None = None
    date_basis: str | None = None
    available_at: datetime | None = None
    availability_basis: str | None = None
    status: FactStatus
    version: int
    model_task: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    created_at: datetime
    updated_at: datetime


async def citable_facts(conn: AsyncConnection, case_id: uuid.UUID, reads: PageReadLog | None) -> list[Fact]:
    """The facts a report may cite. Without a read log nothing is citable (fail closed)."""
    if reads is None:
        return []
    cur = await conn.execute("select named_expert_id from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    if row is None:
        return []
    read = await reads.read_page_ids(conn, case_id, row[0])
    if not read:
        return []
    async with conn.cursor(row_factory=dict_row) as rows:
        await rows.execute(
            "select * from app.facts where case_id = %s and status = any(%s) and page_id = any(%s)"
            " order by created_at, id",
            (case_id, sorted(CITABLE_STATUSES), list(read)),
        )
        return [Fact(**r) for r in await rows.fetchall()]
