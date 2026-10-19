"""Slice F, time (screen 18b): `POST .../time/heartbeat` every 30 seconds while the person works in the case,
`GET .../time` the named expert's time by workflow step, `PATCH .../time/adjust` the expert's correction.

Acceptance (slice F brief): a heartbeat counts only when the tab is visible and there was input in the last
5 minutes; a break of more than 5 minutes starts a new entry (the break is not counted); the stage comes
from the current screen through the pack's workflow steps; adjustments are audited and need
`invoice.create`; totals never go below zero.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from tests.fixtures.report.world import Reads, World, app_for, build
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    yield store
    await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: SupabaseStorage) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads, storage)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


def steps(combo: Combo) -> list[dict[str, Any]]:
    return list(default_library().combine(*combo.codes).workflow_steps())


def screen_and_step(combo: Combo, index: int = 0) -> tuple[str, str]:
    step = steps(combo)[index]
    return str(step["screens"][-1]), str(step["key"])


def beat(screen: str, **extra: Any) -> dict[str, Any]:
    return {"screen": screen, "visible": True, "idle_seconds": 10, **extra}


def entries(admin: psycopg.Connection, case: uuid.UUID) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select person_id, stage, seconds, adjusted_by_expert from app.time_entries where case_id = %s"
        " order by created_at",
        (case,),
    ).fetchall()


async def test_a_visible_active_heartbeat_counts_under_the_screens_workflow_step(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    screen, step = screen_and_step(world.combo)
    client = await client_for(world.expert)
    resp = await client.post(world.url("time/heartbeat"), json=beat(screen))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"counted": True, "stage": step}
    admin.execute(
        "update app.time_entries set started_at = started_at - interval '90 seconds',"
        " ended_at = ended_at - interval '30 seconds' where case_id = %s",
        (world.case,),
    )
    assert (await client.post(world.url("time/heartbeat"), json=beat(screen))).json()["counted"] is True
    rows = entries(admin, world.case)
    assert len(rows) == 1 and rows[0][0] == world.expert and rows[0][1] == step
    assert 89 <= rows[0][2] <= 95, "the entry runs from its first beat to its latest"


async def test_a_hidden_tab_or_an_idle_person_is_not_counted(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    screen, _ = screen_and_step(world.combo)
    client = await client_for(world.expert)
    for body in (beat(screen, visible=False), beat(screen, idle_seconds=301)):
        resp = await client.post(world.url("time/heartbeat"), json=body)
        assert resp.status_code == 200 and resp.json()["counted"] is False, body
    assert entries(admin, world.case) == []


async def test_a_break_of_more_than_five_minutes_starts_a_new_entry(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    screen, _ = screen_and_step(world.combo)
    client = await client_for(world.expert)
    await client.post(world.url("time/heartbeat"), json=beat(screen))
    admin.execute(
        "update app.time_entries set started_at = started_at - interval '10 minutes',"
        " ended_at = ended_at - interval '6 minutes', seconds = 240 where case_id = %s",
        (world.case,),
    )
    await client.post(world.url("time/heartbeat"), json=beat(screen))
    assert [r[2] for r in entries(admin, world.case)] == [240, 0]


async def test_changing_screen_to_another_step_starts_a_new_entry(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    first, step_one = screen_and_step(world.combo, 0)
    second, step_two = screen_and_step(world.combo, 1)
    client = await client_for(world.expert)
    await client.post(world.url("time/heartbeat"), json=beat(first))
    await client.post(world.url("time/heartbeat"), json=beat(second))
    assert [r[1] for r in entries(admin, world.case)] == [step_one, step_two]


async def test_an_unknown_screen_is_refused_and_outsiders_cannot_beat(client_for: ClientFor, world: World) -> None:
    screen, _ = screen_and_step(world.combo)
    resp = await (await client_for(world.expert)).post(world.url("time/heartbeat"), json=beat("no_such_screen"))
    assert resp.status_code == 422
    resp = await (await client_for(world.outsider)).post(world.url("time/heartbeat"), json=beat(screen))
    assert resp.status_code == 404


async def test_totals_show_the_named_experts_time_by_step_with_adjustments(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    screen, step = screen_and_step(world.combo)
    for person in (world.expert, world.viewer):
        await (await client_for(person)).post(world.url("time/heartbeat"), json=beat(screen))
    admin.execute("update app.time_entries set seconds = 600 where case_id = %s", (world.case,))

    expert = await client_for(world.expert)
    resp = await expert.get(world.url("time"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_seconds"] == 600, "only the named expert's time is billed"
    assert [s["stage"] for s in body["stages"]] == [s["key"] for s in steps(world.combo)]
    assert {s["stage"]: s["seconds"] for s in body["stages"]}[step] == 600

    adjust = await expert.patch(world.url("time/adjust"), json={"stage": step, "seconds": -120, "reason": "break"})
    assert adjust.status_code == 200, adjust.text
    assert adjust.json()["total_seconds"] == 480
    too_far = await expert.patch(world.url("time/adjust"), json={"stage": step, "seconds": -481, "reason": "break"})
    assert too_far.status_code == 422
    unknown = await expert.patch(world.url("time/adjust"), json={"stage": "zz_nope", "seconds": 60, "reason": "x"})
    assert unknown.status_code == 422
    audit = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'time.adjusted'", (world.case,)
    ).fetchone()
    assert audit is not None and audit[0] == 1


async def test_adjusting_time_needs_invoice_create(client_for: ClientFor, world: World) -> None:
    _, step = screen_and_step(world.combo)
    body = {"stage": step, "seconds": 60, "reason": "missed"}
    for person in (world.co_expert, world.viewer):
        resp = await (await client_for(person)).patch(world.url("time/adjust"), json=body)
        assert resp.status_code == 403, person
    resp = await (await client_for(world.assistant)).patch(world.url("time/adjust"), json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_seconds"] == 60


async def test_time_names_the_instructing_party_in_the_packs_word(client_for: ClientFor, world: World) -> None:
    """Screens 18 and 18b say who the report went to in the pack's own word, with or without an estimate."""
    parties = default_library().combine(*world.combo.codes).instructing_party_types()
    resp = await (await client_for(world.expert)).get(world.url("time"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["instructing_party"] == (str(parties[0]["label"]) if parties else None)
