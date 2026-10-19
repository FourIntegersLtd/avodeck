"""Slice G: the report-served handler acts as the person the event names (`ReportServed.served_by`).

Slice F sets `served_by` to whoever served. The handler acts as that person, never as whoever made the latest
serving, and an event that names nobody is refused rather than guessed.
"""

# ruff: noqa: F811
from __future__ import annotations

import psycopg
import pytest

from app.core.contracts import events
from app.features.after_serving import events as served_events
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
from tests.support.db import ClientFor

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
    version = await make_version(admin, aconn, world, status="locked")
    events.events.subscribe(events.ReportServed)(seen.append)
    try:
        resp = await (await client_for(world.expert)).post(
            world.url(f"report/versions/{version}/serve"),
            json={"served_on": SERVED_ON.isoformat(), "sent_via": "email"},
        )
    finally:
        events.events.unsubscribe(events.ReportServed, seen.append)
    assert resp.status_code == 200, resp.text
    assert [e.served_by for e in seen] == [world.expert]


async def test_the_handler_acts_as_served_by_whoever_served_last(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: World
) -> None:
    await client_for(world.expert)  # the app is up
    version = await make_version(admin, aconn, world)
    # The newest serving row names someone else: the handler must not look there.
    admin.execute(
        "update app.report_servings set created_by = %s where report_version_id = %s", (world.outsider, version)
    )
    await served_events.handle_served(events.ReportServed(world.case, version, SERVED_ON, world.expert))
    assert pack_deadlines(admin, world) >= 1


async def test_a_served_event_naming_nobody_is_refused(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: World
) -> None:
    await client_for(world.expert)
    version = await make_version(admin, aconn, world)
    admin.execute(
        "update app.report_servings set created_by = %s where report_version_id = %s", (world.expert, version)
    )
    with pytest.raises(ValueError, match="served_by"):
        await served_events.handle_served(events.ReportServed(world.case, version, SERVED_ON))
    assert pack_deadlines(admin, world) == 0


async def test_a_served_event_naming_someone_off_the_case_makes_nothing(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: World
) -> None:
    await client_for(world.expert)
    version = await make_version(admin, aconn, world)
    try:
        await served_events.handle_served(events.ReportServed(world.case, version, SERVED_ON, world.outsider))
    except Exception:  # noqa: BLE001, S110 - refused by the database is fine; nothing may be made
        pass
    assert pack_deadlines(admin, world) == 0
