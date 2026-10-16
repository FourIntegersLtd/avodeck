"""docs/spec/state-machines.md as code: the service refuses any other change with 409 `conflict`,
and the database triggers in migration 0002 refuse it again."""

from __future__ import annotations

import uuid

import psycopg
from psycopg import AsyncConnection, sql

from app.core.errors import CONFLICT_SQLSTATE, LOCKED_SQLSTATE, ApiError, conflict, locked

CASE_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"processing", "withdrawn", "deleted"}),
    "processing": frozenset({"active", "withdrawn", "deleted"}),
    "active": frozenset({"served", "settled", "withdrawn", "deleted"}),
    "served": frozenset({"settled", "withdrawn", "deleted"}),
    "settled": frozenset({"archived", "active", "deleted"}),
    "withdrawn": frozenset({"archived", "deleted"}),
    "archived": frozenset({"active", "deleted"}),
    "deleted": frozenset(),
}

JOB_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running"}),
    "running": frozenset({"done", "failed", "paused"}),
    "failed": frozenset({"queued"}),
    "paused": frozenset({"running"}),
    "done": frozenset(),
}

FACT_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "suggested": frozenset({"verified", "edited", "rejected"}),
    "verified": frozenset({"rejected"}),
    "edited": frozenset({"rejected"}),
    "rejected": frozenset({"verified"}),
}

QUESTION_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "suggested": frozenset({"confirmed", "edited"}),
    "confirmed": frozenset(),
    "edited": frozenset(),
}

# Locking, serving and withdrawing carry more than a status (fingerprint, reason): slice F does them;
# the database trigger (migration 0008) holds the same edges.
REPORT_VERSION_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"locked"}),
    "locked": frozenset({"served"}),
    "served": frozenset({"withdrawn"}),
    "withdrawn": frozenset(),
}


async def _change(
    conn: AsyncConnection, table: str, row_id: uuid.UUID, target: str, transitions: dict[str, frozenset[str]]
) -> None:
    query = sql.SQL("select status from app.{} where id = %s").format(sql.Identifier(table))
    cur = await conn.execute(query, (row_id,))
    row = await cur.fetchone()
    if row is None:
        raise ApiError("not_found", "Not found.", 404)
    current = row[0]
    if target not in transitions.get(current, frozenset()):
        raise conflict()
    try:
        cur = await conn.execute(
            sql.SQL("update app.{} set status = %s where id = %s and status = %s").format(sql.Identifier(table)),
            (target, row_id, current),
        )
    except psycopg.Error as exc:
        if exc.sqlstate == CONFLICT_SQLSTATE:
            raise conflict() from None
        if exc.sqlstate == LOCKED_SQLSTATE:
            raise locked() from None
        raise
    if cur.rowcount != 1:
        raise conflict()  # changed by someone else meanwhile, or not ours to change


async def change_case_status(conn: AsyncConnection, case_id: uuid.UUID, target: str) -> None:
    await _change(conn, "cases", case_id, target, CASE_STATUS_TRANSITIONS)


async def change_job_status(conn: AsyncConnection, job_id: uuid.UUID, target: str) -> None:
    await _change(conn, "jobs", job_id, target, JOB_STATUS_TRANSITIONS)


async def change_fact_status(conn: AsyncConnection, fact_id: uuid.UUID, target: str) -> None:
    await _change(conn, "facts", fact_id, target, FACT_STATUS_TRANSITIONS)


async def change_question_status(conn: AsyncConnection, question_id: uuid.UUID, target: str) -> None:
    await _change(conn, "questions", question_id, target, QUESTION_STATUS_TRANSITIONS)
