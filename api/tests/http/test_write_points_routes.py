"""Screen 14: points for and against one question, and the range note (architecture 5.2, slice E brief).

`GET .../questions/{q_id}/points` lists the points (each a verified fact placed on a side), facts the
expert flagged on screen 09 first, then by side; the side labels come from the case-type pack; and the
citable facts not yet used. The named expert moves a point between sides, accepts it or removes it
with a reason (`PATCH .../points/{id}`, If-Match, audited), or adds an unused fact as a point. The
range note ("what other ... might reasonably do", wording from the pack) is the expert's own words,
saved with If-Match and stored encrypted. Only the named expert writes; everyone on the case reads.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from psycopg import AsyncConnection

from app.core.db import Database
from app.core.packs import default_library
from app.features.write.services import WriteServices, configure
from tests.fixtures.write.world import PLAN, Reads, World, app_for, build
from tests.support.contracts import add_fact
from tests.support.db import ClientFor
from tests.support.packs import BOTH, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


class Flags:
    """Slice B's flags on screen 09 ("use in points"), standing in until B lands."""

    def __init__(self) -> None:
        self.flagged: set[uuid.UUID] = set()

    async def flagged_for_points(self, conn: AsyncConnection, case_id: uuid.UUID) -> set[uuid.UUID]:
        return set(self.flagged)


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
def flags() -> Iterator[Flags]:
    flags = Flags()
    configure(WriteServices(flags=flags))
    yield flags
    configure(None)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


def sides(combo: Combo) -> list[dict[str, str]]:
    return default_library().combine(*combo.codes).point_sides()


def suggest(admin: psycopg.Connection, w: World, fact: uuid.UUID, side: str) -> uuid.UUID:
    row = admin.execute(
        "insert into app.points (case_id, question_id, fact_id, side, origin, fact_version, reason)"
        " values (%s, %s, %s, %s, 'ai', 1, 'dated plan') returning id",
        (w.case, w.question, fact, side),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def audited(admin: psycopg.Connection, w: World, point: uuid.UUID) -> list[str]:
    rows = admin.execute(
        "select action from app.audit_log where case_id = %s and target = %s order by seq", (w.case, f"point:{point}")
    ).fetchall()
    return [r[0] for r in rows]


async def points(client: Any, w: World) -> dict[str, Any]:
    got = await client.get(w.url(f"questions/{w.question}/points"))
    assert got.status_code == 200, got.text
    return dict(got.json())


async def test_the_sides_and_their_labels_come_from_the_pack(
    client_for: ClientFor, world: World, combo: Combo, flags: Flags
) -> None:
    listed = await points(await client_for(world.expert), world)
    assert listed["sides"] == sides(combo)
    assert [s["code"] for s in listed["sides"]] == ["supports_claim", "supports_clinician", "neutral"]
    if combo is LAUNCH:
        assert combo.word("clinician") in listed["sides"][1]["label"]
    prompt = default_library().combine(*combo.codes).range_note_prompt()
    assert listed["range_note"] == {"prompt": prompt, "text": None, "version": 0, "dictated_on": None}


async def test_flagged_facts_come_first_then_by_side_and_unused_facts_are_listed(
    client_for: ClientFor, world: World, admin: psycopg.Connection, flags: Flags
) -> None:
    neutral = suggest(admin, world, world.verified, "neutral")
    flagged = suggest(admin, world, world.result, "supports_claim")
    flags.flagged.add(world.result)
    spare = add_fact(admin, world.case, world.read_page, status="verified", value="safety netting advice given")
    listed = await points(await client_for(world.expert), world)
    assert [p["id"] for p in listed["points"]] == [str(flagged), str(neutral)]
    first = listed["points"][0]
    assert (first["flagged"], first["status"], first["origin"], first["reason"]) == (
        True,
        "suggested",
        "ai",
        "dated plan",
    )
    assert first["fact"]["page_no"] == 1 and first["fact"]["page_id"] == str(world.read_page)
    assert [f["id"] for f in listed["unused"]] == [str(spare)]
    assert listed["unused"][0]["value"] == "safety netting advice given"


async def test_everyone_on_the_case_reads_the_points_and_others_get_404(
    client_for: ClientFor, world: World, flags: Flags
) -> None:
    for person in (world.co_expert, world.supervisor, world.assistant, world.viewer):
        assert (
            await (await client_for(person)).get(world.url(f"questions/{world.question}/points"))
        ).status_code == 200
    outsider = await client_for(world.outsider)
    assert (await outsider.get(world.url(f"questions/{world.question}/points"))).status_code == 404
    expert = await client_for(world.expert)
    assert (await expert.get(world.url(f"questions/{uuid.uuid4()}/points"))).status_code == 404


async def test_moving_accepting_and_removing_a_point_are_audited(
    client_for: ClientFor, world: World, admin: psycopg.Connection, flags: Flags
) -> None:
    point = suggest(admin, world, world.verified, "neutral")
    client = await client_for(world.expert)
    path = world.url(f"points/{point}")
    moved = await client.patch(path, json={"side": "supports_clinician"}, headers={"If-Match": '"1"'})
    assert moved.status_code == 200, moved.text
    assert (moved.json()["side"], moved.json()["version"], moved.headers["etag"]) == ("supports_clinician", 2, '"2"')
    accepted = await client.patch(path, json={"status": "accepted"}, headers={"If-Match": '"2"'})
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    assert (await client.patch(path, json={"status": "dismissed"}, headers={"If-Match": '"3"'})).status_code == 422
    removed = await client.patch(
        path,
        json={"status": "dismissed", "dismissed_reason": "not relevant to this question"},
        headers={"If-Match": '"3"'},
    )
    assert removed.status_code == 200 and removed.json()["status"] == "dismissed"
    assert audited(admin, world, point) == ["point.moved", "point.accepted", "point.dismissed"]
    assert (await client.patch(path, json={"status": "accepted"}, headers={"If-Match": '"3"'})).status_code == 412
    assert (await client.patch(path, json={"side": "somewhere"}, headers={"If-Match": '"4"'})).status_code == 422
    assert (await client.patch(path, json={"status": "suggested"}, headers={"If-Match": '"4"'})).status_code == 422


async def test_only_the_named_expert_changes_or_adds_points(
    client_for: ClientFor, world: World, admin: psycopg.Connection, flags: Flags
) -> None:
    point = suggest(admin, world, world.verified, "neutral")
    for person in (world.co_expert, world.supervisor, world.assistant, world.viewer):
        client = await client_for(person)
        change = await client.patch(
            world.url(f"points/{point}"), json={"status": "accepted"}, headers={"If-Match": '"1"'}
        )
        assert change.status_code == 403
        add = await client.post(
            world.url(f"questions/{world.question}/points"), json={"fact_id": str(world.result), "side": "neutral"}
        )
        assert add.status_code == 403
    outsider = await client_for(world.outsider)
    change = await outsider.patch(
        world.url(f"points/{point}"), json={"status": "accepted"}, headers={"If-Match": '"1"'}
    )
    assert change.status_code == 404
    expert = await client_for(world.expert)
    missing = await expert.patch(
        world.url(f"points/{uuid.uuid4()}"), json={"status": "accepted"}, headers={"If-Match": '"1"'}
    )
    assert missing.status_code == 404


async def test_the_expert_adds_an_unused_citable_fact_as_a_point(
    client_for: ClientFor, world: World, admin: psycopg.Connection, flags: Flags
) -> None:
    client = await client_for(world.expert)
    path = world.url(f"questions/{world.question}/points")
    added = await client.post(path, json={"fact_id": str(world.verified), "side": "supports_claim"})
    assert added.status_code == 201, added.text
    out = added.json()
    assert (out["status"], out["origin"], out["side"], out["reason"]) == ("accepted", "expert", "supports_claim", None)
    assert added.headers["etag"] == '"1"'
    assert audited(admin, world, uuid.UUID(out["id"])) == ["point.added"]
    assert (await client.post(path, json={"fact_id": str(world.verified), "side": "neutral"})).status_code == 409
    for fact in (world.suggested, world.rejected, world.on_unread_page, uuid.uuid4()):
        assert (await client.post(path, json={"fact_id": str(fact), "side": "neutral"})).status_code == 422
    assert (await client.post(path, json={"fact_id": str(world.result), "side": "somewhere"})).status_code == 422


async def test_the_range_note_is_the_experts_words_saved_with_if_match_and_encrypted(
    client_for: ClientFor, world: World, admin: psycopg.Connection, flags: Flags
) -> None:
    client = await client_for(world.expert)
    path = world.url(f"questions/{world.question}/range-note")
    assert (await client.put(path, json={"text": "Some would refer"})).status_code == 428
    saved = await client.put(path, json={"text": "Some would refer"}, headers={"If-Match": '"0"'})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["text"], saved.json()["version"], saved.headers["etag"]) == ("Some would refer", 1, '"1"')
    assert (await client.put(path, json={"text": "Late"}, headers={"If-Match": '"0"'})).status_code == 412
    again = await client.put(path, json={"text": "Most would review " + PLAN}, headers={"If-Match": '"1"'})
    assert again.status_code == 200 and again.json()["version"] == 2
    assert (await points(client, world))["range_note"]["text"] == "Most would review " + PLAN
    row = admin.execute("select text from app.range_notes where question_id = %s", (world.question,)).fetchone()
    assert row is not None and b"review" not in bytes(row[0])
    for person in (world.co_expert, world.supervisor, world.assistant, world.viewer):
        other = await client_for(person)
        assert (await other.put(path, json={"text": "x"}, headers={"If-Match": '"2"'})).status_code == 403


async def test_the_reply_names_the_live_points_and_leaves_out_the_dismissed(
    client_for: ClientFor, world: World, admin: psycopg.Connection, flags: Flags
) -> None:
    kept = suggest(admin, world, world.verified, "neutral")
    gone = suggest(admin, world, world.result, "neutral")
    client = await client_for(world.expert)
    path = world.url(f"points/{gone}")
    removed = await client.patch(
        path,
        json={"status": "dismissed", "dismissed_reason": "not relevant to this question"},
        headers={"If-Match": '"1"'},
    )
    assert removed.status_code == 200, removed.text
    got = (await client.get(world.url(f"questions/{world.question}/points"))).json()
    assert {p["id"] for p in got["points"]} == {str(kept), str(gone)}
    assert [p["id"] for p in got["live"]] == [str(kept)]
