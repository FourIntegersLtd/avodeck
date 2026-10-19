"""Whether a trial pack or offline copy is current (architecture 10.7; S10, F35).

Each is stamped with what it was built from (`stamp`): its hearing's version, the last numbered page, the
newest served version and, for a copy, its court bundle. On reading, `judged` keeps a stored stale mark
(a re-issued court bundle sets one) or compares the stamp with the case now through the core contract, so
an adjourned hearing (slice G), new pages or a newer served version show without any event between slices.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from app.core.contracts import trial_freshness


async def stamp(
    conn: AsyncConnection, case_id: uuid.UUID, hearing_id: uuid.UUID | None, court_bundle_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """The snapshot columns for a pack or copy built now."""
    built = await trial_freshness.built_from(conn, case_id, hearing_id, court_bundle_id)
    return {
        "built_hearing_version": built.hearing_version,
        "built_last_page_no": built.last_page_no,
        "built_served_version_id": built.served_version_id,
    }


async def judged(conn: AsyncConnection, case_id: uuid.UUID, row: dict[str, Any]) -> tuple[bool, str | None]:
    """(stale, reason) for a pack or copy row."""
    if row["stale"]:
        return True, row["stale_reason"]
    reason = await trial_freshness.stale_reason(conn, case_id, trial_freshness.built_from_row(row))
    return reason is not None, reason
