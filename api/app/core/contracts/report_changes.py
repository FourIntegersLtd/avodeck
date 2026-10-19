"""Changes to a served report, write side for other slices (slice G owns `report_changes`; slice I adds one
when the expert changes position in a joint statement).

Rule 8: the served report never changes. A changed position is recorded as one `report_changes` row per
served paragraph, with the old and new words and the reason sealed exactly as slice G seals them, so G's
screens and its supplementary report read them like its own. G's `find_dependents` job is queued to suggest
the other paragraphs the change touches; paragraphs citing the same facts are listed at once, as G does.
Runs on the caller's connection: row-level security allows it only to someone who writes opinions on the
case, and the database refuses a paragraph that is not in that served version.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from psycopg import AsyncConnection

from app.core import audit
from app.core.crypto import CaseCrypto
from app.core.jobs import registry

OLD_PURPOSE = "report_change_old"
NEW_PURPOSE = "report_change_new"
REASON_PURPOSE = "report_change_reason"

ChangeKind = Literal["correction", "change_of_opinion"]


@dataclass(frozen=True)
class NewReportChange:
    served_version_id: uuid.UUID
    paragraph_id: uuid.UUID
    kind: ChangeKind
    old_text: str
    new_text: str
    reason: str


async def add_report_changes(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, changes: list[NewReportChange]
) -> list[uuid.UUID]:
    """Add one row per change and queue G's dependents check; the new rows' ids."""
    ids: list[uuid.UUID] = []
    for change in changes:

        async def seal(text: str, purpose: str) -> bytes:
            return await crypto.encrypt(conn, case_id, text.encode(), purpose=purpose)

        cur = await conn.execute(
            "insert into app.report_changes (case_id, served_version_id, kind, paragraph_id, old_text, new_text,"
            " reason, dependent_paragraph_ids, created_by) values (%s, %s, %s, %s, %s, %s, %s,"
            " coalesce((select array_agg(distinct o.paragraph_id) from app.citations c join app.citations o"
            " on o.report_version_id = c.report_version_id and o.fact_id = c.fact_id"
            " and o.paragraph_id <> c.paragraph_id where c.report_version_id = %s and c.paragraph_id = %s),"
            " '{}'), app.current_person_id()) returning id",
            (
                case_id,
                change.served_version_id,
                change.kind,
                change.paragraph_id,
                await seal(change.old_text, OLD_PURPOSE),
                await seal(change.new_text, NEW_PURPOSE),
                await seal(change.reason, REASON_PURPOSE),
                change.served_version_id,
                change.paragraph_id,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        ids.append(row[0])
        await audit.record(conn, action="report_change.added", target=f"report_change:{row[0]}", case_id=case_id)
    if ids:
        await registry.enqueue(conn, case_id, "find_dependents")
    return ids
