"""BG-16: the served-report event is handled inside the serving request's transaction, so it is never lost.

`ReportServed` today is emitted from a background task after the commit. A subscriber registered with
`subscribe_in_transaction` runs on the request's own connection: what it writes commits with the serving, and a
failure undoes both. This test pins that contract for the event (slices F and G switch to it).
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest
from psycopg import AsyncConnection

from app.core import deferred
from app.core.contracts.events import EventBus, ReportServed
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def _bus(made: list[uuid.UUID]) -> EventBus:
    bus = EventBus()

    @bus.subscribe_in_transaction(ReportServed)
    async def on_served(conn: AsyncConnection, event: ReportServed) -> None:
        made.append(await deferred.defer_call(conn, "zz.served", {"case": str(event.case_id)}))

    return bus


async def test_what_a_served_subscriber_writes_commits_with_the_request(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    person = seed.person()
    made: list[uuid.UUID] = []
    deferred.calls["zz.served"] = _noop
    try:
        async with api_db.request(scope_for(person)) as conn:
            await _bus(made).publish(conn, ReportServed(uuid.uuid4(), uuid.uuid4(), date(2026, 1, 1), person))
    finally:
        deferred.calls.pop("zz.served", None)
    assert admin.execute("select count(*) from app.deferred_work where id = %s", (made[0],)).fetchone() == (1,)


async def test_a_failing_request_takes_the_served_subscribers_writes_with_it(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    person = seed.person()
    made: list[uuid.UUID] = []
    deferred.calls["zz.served"] = _noop
    try:
        with pytest.raises(RuntimeError):
            async with api_db.request(scope_for(person)) as conn:
                await _bus(made).publish(conn, ReportServed(uuid.uuid4(), uuid.uuid4(), date(2026, 1, 1), person))
                raise RuntimeError("the request failed after the event")
    finally:
        deferred.calls.pop("zz.served", None)
    assert admin.execute("select count(*) from app.deferred_work where id = %s", (made[0],)).fetchone() == (0,)


async def _noop(conn: AsyncConnection, args: dict[str, str]) -> None:
    return None
