"""Database access as restricted roles with per-transaction session settings (ADR 0013).

Every request runs in one transaction that starts by setting `app.person_id`, `app.org_ids`
and `app.request_id`; every job in one transaction that sets `app.job_case_id`. Settings are
transaction-local (`set_config(..., true)`, the same as SET LOCAL), so they never leak to the
next user of a pooled connection. RLS policies read them through `app.current_*()`; with
nothing set they see nothing.

Slices never open their own connections; they receive one from `request()` or `job()`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

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
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        # prepare_threshold=None: Supavisor transaction mode cannot keep prepared statements.
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"prepare_threshold": None},
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

    @asynccontextmanager
    async def _transaction(self, settings: dict[str, str]) -> AsyncIterator[AsyncConnection]:
        async with self._pool.connection() as conn, conn.transaction():
            for name, value in settings.items():
                await conn.execute("select set_config(%s, %s, true)", (name, value))
            yield conn
