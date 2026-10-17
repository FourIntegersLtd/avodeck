"""SQL for slice H: the case's close columns, legal holds, deletion certificates and the audit trail.

Runs as the signed-in person (row-level security and the cases triggers repeat every check).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

_CASE = (
    "select id, status, settled_at, retention_policy, delete_after, jurisdiction, case_type, specialty,"
    " app.case_ever_served(id) as ever_served, app.close_withdrawn_on(id) as withdrawn_on"
    " from app.cases where id = %s"
)
_HOLD = "select id, reason, until, set_by, created_at, released_at from app.legal_holds"


async def _one(conn: AsyncConnection, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)  # type: ignore[arg-type]
        return await cur.fetchone()


async def _all(conn: AsyncConnection, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)  # type: ignore[arg-type]
        return await cur.fetchall()


async def today(conn: AsyncConnection) -> date:
    cur = await conn.execute("select current_date")
    row = await cur.fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


async def case(conn: AsyncConnection, case_id: uuid.UUID, *, lock: bool = False) -> dict[str, Any]:
    row = await _one(conn, _CASE + (" for update" if lock else ""), (case_id,))
    assert row is not None  # access was checked first
    return row


async def update_case(conn: AsyncConnection, case_id: uuid.UUID, **values: Any) -> None:
    columns = ", ".join(f"{name} = %s" for name in values)  # names are this module's own constants
    await conn.execute(f"update app.cases set {columns} where id = %s", (*values.values(), case_id))  # type: ignore[arg-type]


async def active_holds(conn: AsyncConnection, case_id: uuid.UUID) -> list[dict[str, Any]]:
    return await _all(conn, _HOLD + " where case_id = %s and released_at is null order by created_at, id", (case_id,))


async def hold(conn: AsyncConnection, case_id: uuid.UUID, hold_id: uuid.UUID) -> dict[str, Any] | None:
    return await _one(conn, _HOLD + " where id = %s and case_id = %s", (hold_id, case_id))


async def add_hold(conn: AsyncConnection, case_id: uuid.UUID, reason: str, until: date | None) -> uuid.UUID:
    hold_id = uuid.uuid4()
    await conn.execute(
        "insert into app.legal_holds (id, case_id, reason, until, set_by)"
        " values (%s, %s, %s, %s, app.current_person_id())",
        (hold_id, case_id, reason, until),
    )
    return hold_id


async def release_hold(conn: AsyncConnection, hold_id: uuid.UUID) -> None:
    await conn.execute(
        "update app.legal_holds set released_at = now(), released_by = app.current_person_id() where id = %s",
        (hold_id,),
    )


async def deletion(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, Any] | None:
    """The certificate row, if this person may see it (it exists from the start of the deletion run)."""
    return await _one(
        conn,
        "select case_id, deleted_at, counts, hashes, log_chain_heads, backup_expiry_on, sent_to"
        " from app.deletion_certificates where case_id = %s",
        (case_id,),
    )


async def deletion_started(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    """Whether the deletion run has started, for any case member (the certificate is not theirs to read)."""
    cur = await conn.execute("select app.close_deletion_started(%s)", (case_id,))
    row = await cur.fetchone()
    return bool(row and row[0])


async def audit_trail(conn: AsyncConnection, case_id: uuid.UUID) -> list[dict[str, Any]]:
    return await _all(
        conn, "select seq, at, action, target, person_id from app.close_case_audit(%s)", (case_id,)
    )


async def export_job(conn: AsyncConnection, case_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any] | None:
    return await _one(
        conn,
        "select id, status from app.jobs where id = %s and case_id = %s and kind = 'export_case'",
        (job_id, case_id),
    )


async def lock_download(conn: AsyncConnection, key: str) -> None:
    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
