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

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from psycopg import AsyncConnection, AsyncCursor
from psycopg.errors import QueryCanceled
from psycopg.pq import TransactionStatus
from psycopg_pool import AsyncConnectionPool, PoolTimeout, TooManyRequests

from app.core.errors import PoolBusy

# Connections kept open and ready, so a request after a quiet spell does not wait for a new one.
DEFAULT_MIN_SIZE = 4

DEFAULT_MAX_SIZE = 10  # the API's pool

# Roles that must never run the app, even if a misconfigured URL points at them.
_PRIVILEGED_ROLES = frozenset({"postgres", "service_role", "supabase_admin", "app_migrator"})


class UnsafeDatabaseRole(RuntimeError):
    """The connection's role could bypass row-level security."""


# SQL statements run in the current request (set to 0 by `RequestLog`, read for the log line and the
# Server-Timing header). A one-item list, so statements run in child tasks are counted too. Only the
# number is kept, never the SQL or its parameters (rule 6).
statement_count: ContextVar[list[int]] = ContextVar("statement_count")
# A job's limit on the pool connections it holds at once (`JobContext.limit_checkouts`): `Database.job()` takes a
# permit while the connection is held. Set inside the handler's context, so only that job's tasks see it.
job_checkouts: ContextVar[asyncio.Semaphore | None] = ContextVar("job_checkouts", default=None)


class _CountingCursor(AsyncCursor[Any]):
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        counter = statement_count.get(None)
        if counter is not None:
            counter[0] += 1
        return await super().execute(*args, **kwargs)


@dataclass(frozen=True)
class RequestScope:
    """Who is acting, taken from the verified session."""

    person_id: uuid.UUID
    org_ids: tuple[uuid.UUID, ...]
    request_id: str


class _Hold:
    """One request's claim on its pooled connection; `released` once `Database.release` gave it back."""

    released = False

    def __init__(self, checkout: AsyncExitStack) -> None:
        self.checkout = checkout


class Database:
    def __init__(
        self,
        dsn: str,
        *,
        min_size: int | None = None,
        max_size: int = DEFAULT_MAX_SIZE,
        acquire_timeout: float = 30.0,
        max_waiting: int = 0,
        statement_timeout_ms: int = 0,
    ) -> None:
        """`acquire_timeout` is how long a caller waits for a free connection and `max_waiting` how many may
        wait at once (0 = any number); past either, the caller gets `PoolBusy` (503 `busy`). A positive
        `statement_timeout_ms` cancels any statement that runs longer, also as `PoolBusy`. The API sets all
        three; the worker keeps the defaults because bundle steps are long."""
        # prepare_threshold=None: Supavisor transaction mode cannot keep prepared statements.
        self.max_size = max_size
        self.statement_timeout_ms = statement_timeout_ms
        self._holds: dict[AsyncConnection, _Hold] = {}  # request connections that may still be given back early
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=min(DEFAULT_MIN_SIZE, max_size) if min_size is None else min_size,
            max_size=max_size,
            open=False,
            timeout=acquire_timeout,
            max_waiting=max_waiting,
            # autocommit: db.py sends BEGIN and COMMIT itself, so BEGIN shares a round trip with the
            # settings (psycopg's own BEGIN waits for its answer). Callers still get one transaction.
            kwargs={"prepare_threshold": None, "autocommit": True, "cursor_factory": _CountingCursor},
        )

    def pool_stats(self) -> dict[str, int]:
        """The pool's own counters (`pool_size`, `pool_available`, `requests_waiting`): numbers only."""
        stats = self._pool.get_stats()
        return {k: int(stats.get(k, 0)) for k in ("pool_size", "pool_available", "requests_waiting")}

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

    @contextmanager
    def _busy_guard(self) -> Iterator[None]:
        """No connection in time, or a statement over its limit, is `PoolBusy` (503 `busy`), never a hang or a 500."""
        try:
            yield
        except (PoolTimeout, TooManyRequests) as exc:
            raise PoolBusy from exc
        except QueryCanceled as exc:
            if not self.statement_timeout_ms:
                raise
            raise PoolBusy from exc

    def _with_limits(self, settings: dict[str, str]) -> dict[str, str]:
        if self.statement_timeout_ms > 0:
            return {**settings, "statement_timeout": str(self.statement_timeout_ms)}
        return settings

    @asynccontextmanager
    async def session_request(
        self,
        resolve_args: Sequence[Any],
        request_id: str,
        after: Callable[[AsyncConnection], Awaitable[None]] | None = None,
    ) -> AsyncIterator[tuple[AsyncConnection, tuple[Any, ...] | None]]:
        with self._busy_guard():
            async with self._session_request(resolve_args, request_id, after) as found:
                yield found

    @asynccontextmanager
    async def _session_request(
        self,
        resolve_args: Sequence[Any],
        request_id: str,
        after: Callable[[AsyncConnection], Awaitable[None]] | None = None,
    ) -> AsyncIterator[tuple[AsyncConnection, tuple[Any, ...] | None]]:
        """`request()` for a signed-in route whose person is not known yet: ONE connection, ONE transaction.

        BEGIN, the settings and `app.session_resolve(*resolve_args)` go out together; `app.person_id` is
        then set from the session row it returns, in the same statement, but only for an active session
        (otherwise it stays unset and RLS shows nothing). Yields the connection and that row (None when
        no session matches). The caller decides what the row means and must still leave normally to keep
        the function's own writes (it ends an idle or expired session, and counts activity): raising
        rolls them back.
        `app.org_ids`, `app.request_id` and `TimeZone` are the same as `request()` sets.
        `after`, if given, is awaited on an idle connection once the transaction has ended (the same one, or a
        borrowed one after `release`), for a count that must survive a rollback or must not hold a lock for the
        request. It must not raise.
        """
        settings = self._with_limits({"app.org_ids": "", "app.request_id": request_id, "TimeZone": "UTC"})
        *lookup, touch = resolve_args
        row: tuple[Any, ...] | None = None
        stack = AsyncExitStack()  # holds the pool's checkout, so `release` can end it before the request does
        conn = await stack.enter_async_context(self._pool.connection())
        hold = _Hold(stack)
        self._holds[conn] = hold
        try:
            try:
                try:
                    async with conn.pipeline():
                        await conn.execute("BEGIN")
                        await _set(conn, settings)
                        # p_touch is always false here: counting activity inside this transaction would keep
                        # the session row locked until the route ends. It is counted below, after the request.
                        cur = await conn.execute(
                            "select r.*, set_config('app.person_id', "
                            "case when r.state = 'active' then r.person_id::text else '' end, true) "
                            "from app.session_resolve(%s, %s, %s, false) r",
                            tuple(lookup),
                        )
                    row = await cur.fetchone()
                    yield conn, (row[:-1] if row is not None else None)
                    if not hold.released:
                        await conn.execute("COMMIT")
                except BaseException:
                    if not hold.released:
                        with anyio.CancelScope(shield=True):  # a dropped client must not leave the transaction open
                            await _rollback(conn)
                    raise
            finally:
                if (after is not None or (touch and row is not None)) and hold.released:
                    # The connection went back to the pool early: borrow one again for what follows.
                    with anyio.CancelScope(shield=True), suppress(Exception):
                        async with self._pool.connection() as again:
                            await self._finish(again, row, lookup, touch, after)
                elif after is not None or (touch and row is not None):
                    with anyio.CancelScope(shield=True):  # nor skip the counts
                        await self._finish(conn, row, lookup, touch, after)
        finally:
            if not hold.released:
                self._holds.pop(conn, None)
                with anyio.CancelScope(shield=True):
                    await stack.aclose()

    @staticmethod
    async def _finish(
        conn: AsyncConnection,
        row: tuple[Any, ...] | None,
        lookup: Sequence[Any],
        touch: bool,
        after: Callable[[AsyncConnection], Awaitable[None]] | None,
    ) -> None:
        """On an idle connection, after the request's transaction: the caller's `after`, then session activity."""
        if after is not None:
            with suppress(Exception):
                await after(conn)
        if touch and row is not None:
            await _count_activity(conn, row, idle=lookup[1], max_age=lookup[2])

    async def release(self, conn: AsyncConnection) -> None:
        """Commit a `session_request` transaction now and give its connection back to the pool, for a route
        about to do slow work (Storage, a slow client's body, CPU) that needs no database meanwhile.

        Only for a connection from `session_request`. The caller must not use `conn` again; its later
        database work opens `request(scope)` as a new, short transaction. Leaving the request normally
        afterwards commits nothing twice and touches nothing, and a second call does nothing."""
        hold = self._holds.get(conn)
        if hold is None or hold.released:
            return
        await conn.execute("COMMIT")
        hold.released = True
        del self._holds[conn]
        with anyio.CancelScope(shield=True):
            await hold.checkout.aclose()

    @asynccontextmanager
    async def anonymous(self, request_id: str = "") -> AsyncIterator[AsyncConnection]:
        """A transaction acting for nobody: RLS shows no rows. Only the narrow security-definer
        functions for the signed-out steps (session lookup, email-change links, failed sign-in
        audit) do anything here."""
        async with self._transaction({"app.request_id": request_id, "TimeZone": "UTC"}) as conn:
            yield conn

    @asynccontextmanager
    async def job(self, case_id: uuid.UUID) -> AsyncIterator[AsyncConnection]:
        settings = {"app.job_case_id": str(case_id), "TimeZone": "UTC"}
        permits = job_checkouts.get()
        if permits is None:
            async with self._transaction(settings) as conn:
                yield conn
            return
        async with permits, self._transaction(settings) as conn:
            yield conn

    async def anonymous_one(
        self, query: str, params: Sequence[Any] = (), request_id: str = ""
    ) -> tuple[Any, ...] | None:
        with self._busy_guard():
            return await self._anonymous_one(query, params, request_id)

    async def _anonymous_one(self, query: str, params: Sequence[Any], request_id: str) -> tuple[Any, ...] | None:
        """One statement in its own `anonymous()` transaction, sent with BEGIN, the settings and
        COMMIT in one round trip; its first row, or None. For the signed-out lookups that run on
        every request (the session behind the cookie)."""
        settings = self._with_limits({"app.request_id": request_id, "TimeZone": "UTC"})
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
        with self._busy_guard():
            async with self._transaction_unguarded(self._with_limits(settings)) as conn:
                yield conn

    @asynccontextmanager
    async def _transaction_unguarded(self, settings: dict[str, str]) -> AsyncIterator[AsyncConnection]:
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


async def _count_activity(conn: AsyncConnection, row: tuple[Any, ...], *, idle: timedelta, max_age: timedelta) -> None:
    """After the request's transaction has ended: one autocommit statement counts the activity of the active
    session `row` (the `session_request` row) if it was last counted over 30 seconds ago, so the session row
    is locked only for that statement. Skipped when the lookup just saw it counted. Never raises."""
    *fields, _person_setting = row
    if fields[-1] != "active" or fields[8] > datetime.now(UTC) - timedelta(seconds=30):
        return
    with suppress(Exception):
        await conn.execute("select app.session_touch(%s, %s, %s)", (fields[0], idle, max_age))


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
