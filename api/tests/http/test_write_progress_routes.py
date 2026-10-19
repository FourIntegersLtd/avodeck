"""The write progress summary (screens 14 and 15): which questions have their points settled and which
have an opinion, so the question tabs, the "done" line and "N of M questions answered" need no guessing.

A question's points are done once it has at least one point kept and none still suggested or stale.
Only keys, flags and counts leave the server (rule 6). Anyone who can see the case may read it.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.packs import default_library
from tests.fixtures.write.world import Reads, World, app_for, build
from tests.support.contracts import add_question
from tests.support.db import ClientFor
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
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


def place(admin: psycopg.Connection, w: World, fact: uuid.UUID, side: str, status: str) -> None:
    admin.execute(
        "insert into app.points (case_id, question_id, fact_id, side, origin, fact_version, status, reason)"
        " values (%s, %s, %s, %s, %s, 1, %s, 'dated plan')",
        (w.case, w.question, fact, side, "expert" if status == "accepted" else "ai", status),
    )


async def progress(client: Any, w: World) -> dict[str, Any]:
    got = await client.get(w.url("write-progress"))
    assert got.status_code == 200, got.text
    body: dict[str, Any] = got.json()
    return body


async def test_progress_follows_points_and_opinions(
    client_for: ClientFor, world: World, admin: psycopg.Connection, combo: Combo
) -> None:
    side = default_library().combine(*combo.codes).point_sides()[0]["code"]
    second = add_question(admin, world.case, number=2, status="confirmed")
    add_question(admin, world.case, number=3)  # not confirmed: not counted
    expert = await client_for(world.expert)

    start = await progress(expert, world)
    assert start["total"] == 2 and start["answered"] == 0
    assert [(q["question_id"], q["points_done"], q["has_opinion"]) for q in start["questions"]] == [
        (str(world.question), False, False),
        (str(second), False, False),
    ]

    place(admin, world, world.verified, side, "suggested")
    assert (await progress(expert, world))["questions"][0]["points_done"] is False
    admin.execute("update app.points set status = 'accepted' where question_id = %s", (world.question,))
    assert (await progress(expert, world))["questions"][0]["points_done"] is True
    place(admin, world, world.result, side, "suggested")
    assert (await progress(expert, world))["questions"][0]["points_done"] is False

    payload: dict[str, object] = {"body": "My view.", "citation_refs": [], "view": None, "provisional": False}
    saved = await expert.put(
        world.url(f"questions/{world.question}/opinion"), json=payload, headers={"If-Match": '"0"'}
    )
    assert saved.status_code == 200, saved.text
    done = await progress(expert, world)
    assert done["answered"] == 1 and done["total"] == 2
    assert done["questions"][0]["has_opinion"] is True and done["questions"][1]["has_opinion"] is False


async def test_progress_is_visible_to_members_and_hidden_from_outsiders(client_for: ClientFor, world: World) -> None:
    assert (await (await client_for(world.assistant)).get(world.url("write-progress"))).status_code == 200
    assert (await (await client_for(world.outsider)).get(world.url("write-progress"))).status_code == 404
