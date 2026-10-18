"""When a page reached the people involved, and where on the page (slice A writes, slice D reads).

Slice A stores each page's availability evidence (received, filed, seen, sent to the clinician) when
it reads the page. Slice D matches it to the facts it belongs to by reading order. Both functions
run on the caller's connection, so row-level security decides what is seen (rule 9).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

AvailabilityKind = Literal["received", "filed", "seen", "sent_to_clinician"]


class AvailabilityEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    page_id: uuid.UUID
    page_no: int
    kind: AvailabilityKind
    date: date | None  # read by code in the pack's date order; None when it could be read two ways
    word_ids: list[str]
    position: int  # the smallest word number (ocr_words.n) among word_ids on the page


async def evidence_for_case(conn: AsyncConnection, case_id: uuid.UUID) -> list[AvailabilityEvidence]:
    """Every piece of evidence in the case, by page number, then date (undated last), then position."""
    cur = await conn.execute(
        """
        select p.id, p.bundle_page_no, e.item ->> 'kind', (e.item ->> 'date')::date,
               array(select jsonb_array_elements_text(e.item -> 'word_ids')),
               coalesce((select min(w.n) from app.ocr_words w
                          where w.page_id = p.id
                            and w.word_id in (select jsonb_array_elements_text(e.item -> 'word_ids'))), 0)
          from app.page_analysis a
          join app.pages p on p.id = a.page_id
          cross join lateral jsonb_array_elements(a.availability_evidence) with ordinality as e(item, ordinality)
         where a.case_id = %s
         order by p.bundle_page_no, (e.item ->> 'date')::date nulls last, 6, e.ordinality
        """,
        (case_id,),
    )
    return [
        AvailabilityEvidence(page_id=r[0], page_no=r[1], kind=r[2], date=r[3], word_ids=list(r[4]), position=r[5])
        for r in await cur.fetchall()
    ]


async def fact_positions(conn: AsyncConnection, case_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """For each fact in the case, the smallest word number (ocr_words.n) among its words on its page."""
    cur = await conn.execute(
        """
        select f.id, min(w.n)
          from app.facts f
          join app.ocr_words w on w.page_id = f.page_id and w.word_id = any (f.word_ids)
         where f.case_id = %s
         group by f.id
        """,
        (case_id,),
    )
    return {r[0]: r[1] for r in await cur.fetchall()}
