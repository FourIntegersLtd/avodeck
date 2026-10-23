"""The audit log: who, for which organisation, on which case, did what to what (architecture 10.1, 10.9).

Rows are append-only for the running app. The database numbers each row and chains it to the
previous one (migration 0004); `find_breaks()` recomputes the chain with the same encoding and
names every row that was changed, removed or put out of order. Targets are IDs such as
`case:<uuid>`, never patient text.

`record()` writes in the request's transaction, so a refused request leaves nothing. A row that must stay
when the request is refused (a refused attempt at a sensitive action) goes through `record_detached()`:
the route layer (`core/http.py`, inside `detached()`) writes it in a transaction of its own, as the same
person, after the request's transaction has ended (committed or rolled back). Writing it after, not
alongside, means it never waits on the chain lock the request may hold.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from psycopg import AsyncConnection

if TYPE_CHECKING:
    from app.core.db import Database, RequestScope

_log = logging.getLogger(__name__)

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


@dataclass(frozen=True)
class _Detached:
    action: str
    target: str
    case_id: uuid.UUID | None
    organisation_id: uuid.UUID | None


_DETACHED: ContextVar[list[_Detached] | None] = ContextVar("audit_detached", default=None)


def record_detached(
    *,
    action: str,
    target: str,
    case_id: uuid.UUID | None = None,
    organisation_id: uuid.UUID | None = None,
) -> None:
    """Keep one row to write after the request's transaction ends, whatever its outcome (inside `detached()`)."""
    pending = _DETACHED.get()
    if pending is None:
        raise RuntimeError("record_detached() runs only inside audit.detached() (a signed-in route)")
    pending.append(_Detached(action, target, case_id, organisation_id))


@asynccontextmanager
async def detached(database: Database, scope: RequestScope) -> AsyncIterator[None]:
    """Around a request's transaction: on the way out, write every `record_detached()` row in a new one."""
    pending: list[_Detached] = []
    token = _DETACHED.set(pending)
    try:
        yield
    finally:
        _DETACHED.reset(token)
        if pending:
            # The request's own answer stands whatever happens here (its transaction has already ended), so
            # a failed write is logged, by action code and error type only, never raised over it.
            try:
                async with database.request(scope) as conn:
                    for row in pending:
                        await record(
                            conn, action=row.action, target=row.target, case_id=row.case_id,
                            organisation_id=row.organisation_id,
                        )  # fmt: skip
            except Exception as exc:  # noqa: BLE001
                _log.error(
                    "detached audit write failed",
                    extra={"actions": [r.action for r in pending], "error": type(exc).__name__},
                )
