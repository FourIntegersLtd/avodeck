"""`core/notify.CaseNotifier`: one dedicated LISTEN connection that wakes the open streams of a case (PR 8)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.notify import LISTENER_APPLICATION_NAME, CaseNotifier

pytestmark = pytest.mark.anyio


@pytest.fixture
async def notifier(api_dsn: str) -> AsyncIterator[CaseNotifier]:
    n = CaseNotifier(api_dsn, backoff_min=0.05, backoff_max=0.2, ping_seconds=0.2)
    await n.start()
    await n.wait_ready(10)
    yield n
    await n.stop()


def _notify(admin: psycopg.Connection, payload: str) -> None:
    admin.execute("select pg_notify('case_jobs', %s)", (payload,))


async def _woke(event: asyncio.Event, within: float = 2.0) -> bool:
    try:
        await asyncio.wait_for(event.wait(), within)
    except TimeoutError:
        return False
    return True


async def test_a_notify_wakes_only_the_streams_of_that_case(notifier: CaseNotifier, admin: psycopg.Connection) -> None:
    mine, other = uuid.uuid4(), uuid.uuid4()
    with notifier.watch(mine) as mine_a, notifier.watch(mine) as mine_b, notifier.watch(other) as other_event:
        mine_a.clear()
        mine_b.clear()
        other_event.clear()
        _notify(admin, str(mine))
        assert await _woke(mine_a) and await _woke(mine_b)
        assert not other_event.is_set()


async def test_a_payload_that_is_not_a_case_id_is_ignored(notifier: CaseNotifier, admin: psycopg.Connection) -> None:
    case = uuid.uuid4()
    with notifier.watch(case) as event:
        event.clear()
        _notify(admin, "not-a-uuid")
        _notify(admin, "")
        assert not await _woke(event, 0.4)
        _notify(admin, str(case))
        assert await _woke(event), "and the listener is still alive afterwards"


async def test_leaving_a_watch_removes_the_subscription(notifier: CaseNotifier) -> None:
    case = uuid.uuid4()
    with notifier.watch(case):
        assert notifier.watching(case) == 1
    assert notifier.watching(case) == 0


async def test_the_listener_reconnects_after_its_connection_drops_and_wakes_every_stream_once(
    notifier: CaseNotifier, admin: psycopg.Connection
) -> None:
    case = uuid.uuid4()
    with notifier.watch(case) as event:
        event.clear()
        killed = admin.execute(
            "select count(pg_terminate_backend(pid)) from pg_stat_activity where application_name = %s",
            (LISTENER_APPLICATION_NAME,),
        ).fetchone()
        assert killed is not None and killed[0] == 1
        assert await _woke(event, 5), "after the reconnect every stream looks once, so nothing was missed"
        await notifier.wait_ready(5)
        event.clear()
        _notify(admin, str(case))
        assert await _woke(event), "and it is listening again"


async def test_a_notifier_that_cannot_connect_keeps_trying_and_stops_cleanly() -> None:
    n = CaseNotifier("postgresql://nobody:x@127.0.0.1:1/none", backoff_min=0.05, backoff_max=0.1, ping_seconds=0.2)
    await n.start()
    await asyncio.sleep(0.3)
    assert not n.ready
    await asyncio.wait_for(n.stop(), 3)
