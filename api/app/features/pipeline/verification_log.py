"""Who changed a fact's status, when and why, for the changes the pipeline's own routes make (PV-04).

A page set aside rejects every fact on it, and undoing that checks the earlier facts again: both change a fact's
status, so both leave a row in `app.fact_verifications` (the same hash-chained log the reader writes), made in the
same transaction as the status change. The reason is a short code (never page text). Existing actions are used
(`not_right`, `correct`); the log trigger assigns the sequence number, the time and the hash.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from psycopg import AsyncConnection

PAGE_SET_ASIDE = "page_set_aside"
QUARANTINE_UNDONE = "quarantine_undone"
DATE_REPAIR = "date_repair"


async def log_status_change(
    conn: AsyncConnection,
    fact_ids: Sequence[uuid.UUID],
    *,
    person_id: uuid.UUID,
    action: str,
    reason: str,
) -> None:
    """One log row per fact, by `person_id`. Only the status changes, so each row carries the whole fact (value,
    details, cited words, page, date and how it was read) on both sides, as it stands (codes only in `reason`)."""
    if not fact_ids:
        return
    await conn.execute(
        "insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value, old_fields,"
        " new_fields, old_word_ids, new_word_ids, old_page_id, new_page_id, old_event_date, new_event_date,"
        " old_date_basis, new_date_basis, reason)"
        " select f.case_id, f.id, %s, %s, f.value, f.value, f.fields, f.fields, f.word_ids, f.word_ids, f.page_id,"
        " f.page_id, f.event_date, f.event_date, f.date_basis, f.date_basis, %s"
        " from app.facts f where f.id = any(%s) order by f.id",
        (person_id, action, reason, list(fact_ids)),
    )


async def log_date_repair(
    conn: AsyncConnection,
    fact_id: uuid.UUID,
    *,
    old_word_ids: Sequence[str],
    old_date: dt.date | None,
    old_basis: str | None,
) -> None:
    """A checked fact the pipeline dated (and whose cited words it widened to take in the date's words, never
    dropping the fact's own): a `remapped` row with no person (the job did it), keeping the words and the date before
    and after. The new values are read from the fact as it now stands, so the row cannot disagree with it (PV-02)."""
    await conn.execute(
        "insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value, old_fields,"
        " new_fields, old_word_ids, new_word_ids, old_page_id, new_page_id, old_event_date, new_event_date,"
        " old_date_basis, new_date_basis, reason)"
        " select f.case_id, f.id, null, 'remapped', f.value, f.value, f.fields, f.fields, %s, f.word_ids, f.page_id,"
        " f.page_id, %s, f.event_date, %s, f.date_basis, %s from app.facts f where f.id = %s",
        (list(old_word_ids), old_date, old_basis, DATE_REPAIR, fact_id),
    )
