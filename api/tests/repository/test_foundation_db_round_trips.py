"""core/db.py costs few round trips: the cloud database is 20-70 ms away, so each one is felt.

A request transaction sends BEGIN and every session setting together (one round trip), then the
handler's queries, then COMMIT. Looking up the session cookie is one round trip in all. The settings
stay transaction-local (ADR 0013): visible inside, gone from the pooled connection afterwards.
Round trips are counted by a byte-forwarding proxy between the pool and Postgres: each time the
client speaks after the server has answered is one more trip.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core import step_progress
from app.core.auth import session as sessions
from app.core.db import Database, RequestScope
from app.core.errors import ApiError

pytestmark = pytest.mark.anyio

PERSON = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
ORG = uuid.UUID("00000000-0000-4000-8000-0000000000fa")
CASE = uuid.UUID("00000000-0000-4000-8000-0000000000ca")


class Turns:
    """Counts round trips seen by the proxy."""

    def __init__(self) -> None:
        self.count = 0
        self._server_spoke_last = True

    def client(self) -> None:
        if self._server_spoke_last:
            self.count += 1
        self._server_spoke_last = False

    def server(self) -> None:
        self._server_spoke_last = True


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, seen: Callable[[], None]) -> None:
    try:
        while data := await reader.read(65536):
            seen()
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


@pytest.fixture
async def counted(api_dsn: str) -> AsyncIterator[tuple[Callable[..., Database], Turns]]:
    params = conninfo_to_dict(api_dsn)
    host, port = str(params.get("host") or "127.0.0.1"), int(params.get("port") or 5432)
    turns = Turns()
    tasks: set[asyncio.Task[None]] = set()

    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        server_r, server_w = await asyncio.open_connection(host, port)
        for task in (_pipe(client_r, server_w, turns.client), _pipe(server_r, client_w, turns.server)):
            tasks.add(asyncio.ensure_future(task))

    proxy = await asyncio.start_server(handle, "127.0.0.1", 0)
    proxy_port = proxy.sockets[0].getsockname()[1]
    dsn = make_conninfo(api_dsn, host="127.0.0.1", port=str(proxy_port))
    opened: list[Database] = []

    def make(**kwargs: int) -> Database:
        database = Database(dsn, **{"max_size": 1, **kwargs})
        opened.append(database)
        return database

    try:
        yield make, turns
    finally:
        for database in opened:
            await database.close()
        for task in tasks:
            task.cancel()
        proxy.close()


async def measure(turns: Turns, work: Callable[[], Awaitable[object]]) -> int:
    before = turns.count
    await work()
    return turns.count - before


def scope() -> RequestScope:
    return RequestScope(person_id=PERSON, org_ids=(ORG,), request_id="req-rt-1")


async def test_a_request_with_one_query_costs_three_round_trips(counted: tuple[Callable[..., Database], Turns]) -> None:
    make, turns = counted
    db = make()
    await db.open()

    async def one_query() -> None:
        async with db.request(scope()) as conn:
            await (await conn.execute("select 1")).fetchone()

    # BEGIN + every setting together, the query, COMMIT (it was 7: BEGIN, four settings, query, COMMIT).
    assert await measure(turns, one_query) == 3


async def test_a_job_with_one_query_costs_three_round_trips(counted: tuple[Callable[..., Database], Turns]) -> None:
    make, turns = counted
    db = make()
    await db.open()

    async def one_query() -> None:
        async with db.job(CASE) as conn:
            await (await conn.execute("select 1")).fetchone()

    assert await measure(turns, one_query) == 3


async def test_looking_up_a_session_cookie_is_one_round_trip(counted: tuple[Callable[..., Database], Turns]) -> None:
    make, turns = counted
    db = make()
    await db.open()

    async def resolve() -> None:
        with pytest.raises(ApiError) as refused:
            await sessions.resolve(db, "no-such-token", touch=True)
        assert refused.value.code == "not_signed_in"

    # BEGIN, settings, the lookup and COMMIT go together (it was 5).
    assert await measure(turns, resolve) == 1


async def test_counting_every_step_meter_is_one_round_trip(counted: tuple[Callable[..., Database], Turns]) -> None:
    make, turns = counted
    db = make()
    await db.open()
    steps = [
        {"key": "a", "meters": ["questions_confirmed"]},
        {"key": "b", "meters": ["pages_read", "facts_decided"]},
        {"key": "c", "meters": ["questions_with_opinion", "report_stages", "unknown_meter"]},
        {"key": "d"},
    ]
    out: list[dict[str, object]] = []

    async def count() -> None:
        async with db.job(CASE) as conn:
            before = turns.count
            out.extend(await step_progress.step_counts(conn, CASE, steps))
            counted_trips.append(turns.count - before)

    counted_trips: list[int] = []
    await count()
    assert counted_trips == [1]  # one query for all five meters (it was one per meter)
    assert [s["key"] for s in out] == ["a", "b", "c", "d"]
    assert out[1] == {"key": "b", "done": 0, "total": 0, "percent": None}
    assert out[2] == {"key": "c", "done": 0, "total": 4, "percent": 0}
    assert out[3] == {"key": "d", "done": None, "total": None, "percent": None}


async def test_settings_are_visible_inside_and_gone_after_on_the_same_connection(api_dsn: str) -> None:
    db = Database(api_dsn, max_size=1)
    await db.open()
    try:
        async with db.job(CASE) as conn:
            zone_before = (await (await conn.execute("show timezone")).fetchone() or ("",))[0]
        async with db.request(scope()) as conn:
            cur = await conn.execute(
                "select app.current_person_id(), app.current_org_ids(), current_setting('app.request_id', true),"
                " current_setting('TimeZone')"
            )
            assert await cur.fetchone() == (PERSON, [ORG], "req-rt-1", "UTC")
        # max_size=1: the same pooled connection, outside any of our transactions now.
        async with db._pool.connection() as raw:  # noqa: SLF001 - the pool's own connection, unscoped
            cur = await raw.execute(
                "select nullif(current_setting('app.person_id', true), ''), nullif(current_setting('app.org_ids', true), ''),"
                " nullif(current_setting('app.request_id', true), ''), nullif(current_setting('app.job_case_id', true), '')"
            )
            assert await cur.fetchone() == (None, None, None, None)
            await raw.rollback()
        async with db.job(CASE) as conn:
            assert (await (await conn.execute("show timezone")).fetchone() or ("",))[0] == zone_before
    finally:
        await db.close()


async def test_a_failed_setup_leaves_the_pooled_connection_clean(api_dsn: str) -> None:
    db = Database(api_dsn, max_size=1)
    await db.open()
    try:
        with pytest.raises(Exception):  # noqa: B017, PT011 - any database error from a bad setting
            async with db._transaction({"app.person_id": str(PERSON), "TimeZone": "Not/AZone"}):  # noqa: SLF001
                pass
        async with db.job(CASE) as conn:
            cur = await conn.execute("select app.current_person_id(), app.current_job_case_id()")
            assert await cur.fetchone() == (None, CASE)
    finally:
        await db.close()
