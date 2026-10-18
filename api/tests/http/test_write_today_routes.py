"""Today (screen 03): slice E's "needs you" items (slice E brief).

- Questions without an opinion: on each case where the person is the named expert, how many confirmed
  questions have no saved opinion yet; it opens screen 15 and goes once each has one.
- Points against not yet considered: on each case where the person is the named expert, how many
  suggested or stale points wait for the expert's decision; it opens screen 14 and goes once each is
  accepted or removed.
Only the named expert writes, so no one else sees them. Items carry i18n keys and counts only (rule 6).
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


async def items(client: Any, kind: str) -> list[dict[str, Any]]:
    got = await client.get("/v1/today")
    assert got.status_code == 200, got.status_code
    return [i for i in got.json()["needs_you"] if i["kind"] == kind]


def suggest(admin: psycopg.Connection, w: World, fact: uuid.UUID, side: str) -> uuid.UUID:
    row = admin.execute(
        "insert into app.points (case_id, question_id, fact_id, side, origin, fact_version, reason)"
        " values (%s, %s, %s, %s, 'ai', 1, 'dated plan') returning id",
        (w.case, w.question, fact, side),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


async def test_questions_without_an_opinion_until_each_has_one(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    add_question(admin, world.case, number=2)  # not confirmed: no opinion is due on it yet
    expert = await client_for(world.expert)
    [item] = await items(expert, "write_questions_without_opinion")
    assert item["case_id"] == str(world.case)
    assert item["screen"] == "S15" and item["count"] == 1
    assert item["label_key"].startswith("write.") and item["action_key"].startswith("write.")
    for person in (world.co_expert, world.supervisor, world.assistant, world.viewer, world.outsider):
        assert await items(await client_for(person), "write_questions_without_opinion") == []

    payload = {"body": "My view.", "citation_refs": [], "view": None, "provisional": False}
    saved = await expert.put(
        world.url(f"questions/{world.question}/opinion"), json=payload, headers={"If-Match": '"0"'}
    )
    assert saved.status_code == 200, saved.text
    assert await items(expert, "write_questions_without_opinion") == []


async def test_points_not_considered_until_each_is_decided(
    client_for: ClientFor, world: World, admin: psycopg.Connection, combo: Combo
) -> None:
    side = default_library().combine(*combo.codes).point_sides()[0]["code"]
    expert = await client_for(world.expert)
    assert await items(expert, "write_points_not_considered") == []
    first, second = suggest(admin, world, world.verified, side), suggest(admin, world, world.result, side)

    [item] = await items(expert, "write_points_not_considered")
    assert item["case_id"] == str(world.case)
    assert item["screen"] == "S14" and item["count"] == 2
    assert item["label_key"].startswith("write.") and item["action_key"].startswith("write.")
    for person in (world.co_expert, world.assistant, world.outsider):
        assert await items(await client_for(person), "write_points_not_considered") == []

    accepted = await expert.patch(
        world.url(f"points/{first}"), json={"status": "accepted"}, headers={"If-Match": '"1"'}
    )
    assert accepted.status_code == 200, accepted.text
    [item] = await items(expert, "write_points_not_considered")
    assert item["count"] == 1
    removed = await expert.patch(
        world.url(f"points/{second}"),
        json={"status": "dismissed", "dismissed_reason": "not relevant"},
        headers={"If-Match": '"1"'},
    )
    assert removed.status_code == 200, removed.text
    assert await items(expert, "write_points_not_considered") == []
