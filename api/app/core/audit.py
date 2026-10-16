"""The audit log: who, for which organisation, on which case, did what to what (architecture 10.1, 10.9).

Rows are append-only for the running app. The database numbers each row and chains it to the
previous one (migration 0004); `find_breaks()` recomputes the chain with the same encoding and
names every row that was changed, removed or put out of order. Targets are IDs such as
`case:<uuid>`, never patient text.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg import AsyncConnection

GENESIS = "0" * 64
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SEP = "\x1f"


@dataclass(frozen=True)
class AuditRow:
    seq: int
    person_id: uuid.UUID | None
    organisation_id: uuid.UUID | None
    case_id: uuid.UUID | None
    action: str
    target: str
    at: datetime
    prev_hash: str
    hash: str


def row_hash(row: AuditRow, prev_hash: str) -> str:
    """Must match `app.audit_hash()` in migration 0004."""
    micros = (row.at - _EPOCH) // timedelta(microseconds=1)
    fields = [
        str(row.seq),
        str(row.person_id or ""),
        str(row.organisation_id or ""),
        str(row.case_id or ""),
        row.action,
        row.target,
        str(micros),
        prev_hash,
    ]
    return hashlib.sha256(_SEP.join(fields).encode()).hexdigest()


def find_breaks(rows: Iterable[AuditRow]) -> list[int]:
    """The `seq` of every row whose link or own hash does not verify. Give the whole chain, in order."""
    breaks: list[int] = []
    expected_prev = GENESIS
    for row in rows:
        if row.prev_hash != expected_prev or row.hash != row_hash(row, row.prev_hash):
            breaks.append(row.seq)
        expected_prev = row.hash
    return breaks


async def record(
    conn: AsyncConnection,
    *,
    action: str,
    target: str,
    case_id: uuid.UUID | None = None,
    organisation_id: uuid.UUID | None = None,
) -> None:
    """Append one row as the person (or job) the transaction acts for."""
    await conn.execute(
        "insert into app.audit_log (person_id, organisation_id, case_id, action, target)"
        " values (app.current_person_id(), %s, %s, %s, %s)",
        (organisation_id, case_id, action, target),
    )
