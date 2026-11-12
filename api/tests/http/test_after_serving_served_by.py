"""Slice G: the report-served handler runs inside the serving transaction, as the person who served (BG-16).

Slice F publishes `ReportServed` on the serve request's own connection (`events.publish`); the handler does its work
there under row-level security as that person, so a rollback undoes it and a failure undoes the serving. An event
that names nobody is refused rather than guessed.
"""

# ruff: noqa: F811
from __future__ import annotations

import psycopg
import pytest
from psycopg import AsyncConnection

from app.core.contracts import events
from app.core.db import Database
from tests.fixtures.test_after_serving_world import (  # noqa: F401
    SERVED_ON,
    World,
    aconn,
    app,
    combo,
    mailer,
    make_version,
    model,
    ocr,
    reads,
    services,
    storage,
    world,
)
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio


def pack_deadlines(admin: psycopg.Connection, w: World) -> int:
    row = admin.execute(
        "select count(*) from app.deadlines where case_id = %s and created_from = 'pack'", (w.case,)
    ).fetchone()
    return int(row[0]) if row else 0


async def test_slice_f_names_who_served(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: World
) -> None:
    seen: list[events.ReportServed] = []

    async def listen(conn: AsyncConnection, event: events.ReportServed) -> None:
        seen.append(event)

    version = await make_version(admin, aconn, world, status="locked")
    events.events.subscribe_in_transaction(events.ReportServed)(listen)
    try:
        resp = await (await client_for(world.expert)).post(
            world.url(f"report/versions/{version}/serve"),
            json={"served_on": SERVED_ON.isoformat(), "sent_via": "email"},
        )
    finally:
        events.events.unsubscribe(events.ReportServed, listen)
    assert resp.status_code == 200, resp.text
    assert [e.served_by for e in seen] == [world.expert]


async def test_publishing_served_makes_the_deadlines_in_that_transaction(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, api_db: Database, world: World
) -> None:
    await client_for(world.expert)  # the app is up
    version = await make_version(admin, aconn, world)
    # The newest serving row names someone else: the handler must not look there.
    admin.execute(
        "update app.report_servings set created_by = %s where report_version_id = %s", (world.outsider, version)
    )
    async with api_db.request(scope_for(world.expert)) as conn:
        await events.events.publish(conn, events.ReportServed(world.case, version, SERVED_ON, world.expert))
        cur = await conn.execute("select count(*) from app.deadlines where case_id = %s", (world.case,))
        row = await cur.fetchone()
        assert row is not None and row[0] >= 1  # made on the caller's connection, before it commits
    assert pack_deadlines(admin, world) >= 1


async def test_a_rollback_after_publishing_served_undoes_the_deadlines(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, api_db: Database, world: World
) -> None:
    await client_for(world.expert)
    version = await make_version(admin, aconn, world)
    with pytest.raises(RuntimeError):
        async with api_db.request(scope_for(world.expert)) as conn:
            await events.events.publish(conn, events.ReportServed(world.case, version, SERVED_ON, world.expert))
            raise RuntimeError("the serving failed after the event")
    assert pack_deadlines(admin, world) == 0


async def test_a_served_event_naming_nobody_is_refused(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, api_db: Database, world: World
) -> None:
    await client_for(world.expert)
    version = await make_version(admin, aconn, world)
    admin.execute(
        "update app.report_servings set created_by = %s where report_version_id = %s", (world.expert, version)
    )
    with pytest.raises(ValueError, match="served_by"):
        async with api_db.request(scope_for(world.expert)) as conn:
            await events.events.publish(conn, events.ReportServed(world.case, version, SERVED_ON))
    assert pack_deadlines(admin, world) == 0


async def test_a_served_event_naming_someone_off_the_case_makes_nothing(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, api_db: Database, world: World
) -> None:
    await client_for(world.expert)
    version = await make_version(admin, aconn, world)
    try:
        async with api_db.request(scope_for(world.outsider)) as conn:
            await events.events.publish(conn, events.ReportServed(world.case, version, SERVED_ON, world.outsider))
    except Exception:  # noqa: BLE001, S110 - refused by the database is fine; nothing may be made
        pass
    assert pack_deadlines(admin, world) == 0
