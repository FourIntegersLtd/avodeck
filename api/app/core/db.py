"""Database access as restricted roles with per-transaction session settings (ADR 0013).

Every request runs in one transaction that starts by setting `app.person_id`, `app.org_ids`
and `app.request_id`; every job in one transaction that sets `app.job_case_id`. Settings are
transaction-local (`set_config(..., true)`, the same as SET LOCAL), so they never leak to the
next user of a pooled connection. RLS policies read them through `app.current_*()`; with
nothing set they see nothing.

Slices never open their own connections; they receive one from `request()` or `job()`.

Speed: the database is a network hop away, so each transaction's setup is one round trip. BEGIN and
a single `select set_config(...), set_config(...)` go out together in pipeline mode, and the
transaction is open with its settings before the caller's first query. Pooled connections are in
autocommit mode only so that db.py can send BEGIN and COMMIT itself: every caller still runs inside
one transaction, and `conn.transaction()` inside it is a savepoint as before. If anything fails, the
transaction is rolled back before the connection goes back to the pool.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus
from psycopg_pool import AsyncConnectionPool

# Connections kept open and ready, so a request after a quiet spell does not wait for a new one.
DEFAULT_MIN_SIZE = 4

# Roles that must never run the app, even if a misconfigured URL points at them.
_PRIVILEGED_ROLES = frozenset({"postgres", "service_role", "supabase_admin", "app_migrator"})


class UnsafeDatabaseRole(RuntimeError):
    """The connection's role could bypass row-level security."""


@dataclass(frozen=True)
class RequestScope:
    """Who is acting, taken from the verified session."""

    person_id: uuid.UUID
    org_ids: tuple[uuid.UUID, ...]
    request_id: str


class Database:
    def __init__(self, dsn: str, *, min_size: int | None = None, max_size: int = 10) -> None:
        # prepare_threshold=None: Supavisor transaction mode cannot keep prepared statements.
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=min(DEFAULT_MIN_SIZE, max_size) if min_size is None else min_size,
            max_size=max_size,
            open=False,
            # autocommit: db.py sends BEGIN and COMMIT itself, so BEGIN shares a round trip with the
            # settings (psycopg's own BEGIN waits for its answer). Callers still get one transaction.
            kwargs={"prepare_threshold": None, "autocommit": True},
        )

    async def open(self) -> None:
        """Open the pool and refuse to run as any role that could bypass RLS."""
        await self._pool.open(wait=True)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select rolname, rolsuper or rolbypassrls from pg_roles where rolname = current_user"
            )
            row = await cur.fetchone()
        if row is None or row[1] or row[0] in _PRIVILEGED_ROLES:
            await self._pool.close()
            raise UnsafeDatabaseRole("the app must connect as a restricted role (app_api or app_worker)")

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def request(self, scope: RequestScope) -> AsyncIterator[AsyncConnection]:
        settings = {
            "app.person_id": str(scope.person_id),
            "app.org_ids": ",".join(str(o) for o in scope.org_ids),
            "app.request_id": scope.request_id,
            "TimeZone": "UTC",
        }
        async with self._transaction(settings) as conn:
            yield conn

    @asynccontextmanager
    async def anonymous(self, request_id: str = "") -> AsyncIterator[AsyncConnection]:
        """A transaction acting for nobody: RLS shows no rows. Only the narrow security-definer
        functions for the signed-out steps (session lookup, email-change links, failed sign-in
        audit) do anything here."""
        async with self._transaction({"app.request_id": request_id, "TimeZone": "UTC"}) as conn:
            yield conn

    @asynccontextmanager
    async def job(self, case_id: uuid.UUID) -> AsyncIterator[AsyncConnection]:
        async with self._transaction({"app.job_case_id": str(case_id), "TimeZone": "UTC"}) as conn:
            yield conn

    async def anonymous_one(
        self, query: str, params: Sequence[Any] = (), request_id: str = ""
    ) -> tuple[Any, ...] | None:
        """One statement in its own `anonymous()` transaction, sent with BEGIN, the settings and
        COMMIT in one round trip; its first row, or None. For the signed-out lookups that run on
        every request (the session behind the cookie)."""
        settings = {"app.request_id": request_id, "TimeZone": "UTC"}
        async with self._pool.connection() as conn:
            try:
                async with conn.pipeline():
                    await conn.execute("BEGIN")
                    await _set(conn, settings)
                    cur = await conn.execute(query, params)
                    await conn.execute("COMMIT")
            except BaseException:
                await _rollback(conn)
                raise
            return await cur.fetchone()

    @asynccontextmanager
    async def _transaction(self, settings: dict[str, str]) -> AsyncIterator[AsyncConnection]:
        async with self._pool.connection() as conn:
            try:
                # BEGIN and the settings in one round trip; the transaction outlives the pipeline.
                async with conn.pipeline():
                    await conn.execute("BEGIN")
                    await _set(conn, settings)
                yield conn
                await conn.execute("COMMIT")
            except BaseException:
                await _rollback(conn)
                raise


async def _rollback(conn: AsyncConnection) -> None:
    """End a failed transaction before the connection goes back to the pool. A broken connection is
    left for the pool to discard, and the original error is the one raised."""
    if conn.closed or conn.info.transaction_status == TransactionStatus.IDLE:
        return
    with suppress(Exception):
        await conn.execute("ROLLBACK")


async def _set(conn: AsyncConnection, settings: dict[str, str]) -> None:
    """Every setting, transaction-local (`set_config(..., true)`), in one statement."""
    calls = ", ".join("set_config(%s, %s, true)" for _ in settings)
    params = [part for item in settings.items() for part in item]
    await conn.execute(f"select {calls}", params)  # noqa: S608 (placeholders only)
