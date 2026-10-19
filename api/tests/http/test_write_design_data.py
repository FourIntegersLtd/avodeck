"""The server data the S14 and S15 boards need (slice E brief): question short titles, the kind's label
from the case type's question types, when a range note was first written, and where the flagged
sentence sits in the opinion text. Runs under the launch and the fixture packs; no jurisdiction word is
written here."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.packs import default_library
from app.features.write.common import short_title
from tests.fixtures.write.world import Reads, World, app_for, build
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LONG = "Did the assessment on the fifteenth day, including the examination, meet the standard expected?"


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


def test_a_short_title_is_the_first_clause_in_about_forty_characters() -> None:
    assert short_title("Was the standard met?") == "Was the standard met"
    assert short_title(LONG) == "Did the assessment on the fifteenth day"
    assert short_title("Was the delay in referring, in your view, a breach?") == "Was the delay in referring"
    long_run = "word " * 30
    shortened = short_title(long_run)
    assert len(shortened) <= 40 and shortened.endswith("…") and "  " not in shortened
    assert short_title("   ") == ""


def set_question(admin: psycopg.Connection, w: World, kind: str, text: str) -> None:
    admin.execute("update app.questions set kind = %s, confirmed_text = %s where id = %s", (kind, text, w.question))


async def test_progress_carries_each_questions_short_title(
    client_for: ClientFor, world: World, admin: psycopg.Connection, combo: Combo
) -> None:
    set_question(admin, world, "zz_question", LONG)
    got = await (await client_for(world.expert)).get(world.url("write-progress"))
    assert got.status_code == 200, got.text
    assert got.json()["questions"][0]["short_title"] == "Did the assessment on the fifteenth day"


async def test_points_and_workspace_name_the_question_and_its_kind_from_the_pack(
    client_for: ClientFor, world: World, admin: psycopg.Connection, combo: Combo
) -> None:
    kinds = default_library().combine(*combo.codes).question_types()
    first = kinds[0]
    set_question(admin, world, str(first["key"]), LONG)
    client = await client_for(world.expert)
    expected = {
        "number": 1,
        "short_title": "Did the assessment on the fifteenth day",
        "kind": first["key"],
        "kind_label_key": first["label_key"],
        "para_ref": None,
    }
    points = await client.get(world.url(f"questions/{world.question}/points"))
    workspace = await client.get(world.url(f"questions/{world.question}/workspace"))
    assert points.json()["question"] == expected
    assert workspace.json()["question"] == expected


async def test_a_kind_the_pack_does_not_list_has_no_label_key(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    set_question(admin, world, "not_a_kind", LONG)
    got = await (await client_for(world.expert)).get(world.url(f"questions/{world.question}/points"))
    assert got.json()["question"]["kind"] == "not_a_kind" and got.json()["question"]["kind_label_key"] is None


async def test_a_range_note_remembers_the_day_it_was_first_written(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    client = await client_for(world.expert)
    path = world.url(f"questions/{world.question}/range-note")
    first = await client.put(path, json={"text": "Some would wait"}, headers={"If-Match": '"0"'})
    assert first.status_code == 200, first.text
    on = first.json()["dictated_on"]
    assert on is not None
    admin.execute(
        "update app.range_notes set created_at = '2024-10-14T09:00:00Z' where question_id = %s", (world.question,)
    )
    version = admin.execute("select version from app.range_notes where question_id = %s", (world.question,)).fetchone()
    assert version is not None
    second = await client.put(path, json={"text": "Some would wait a week"}, headers={"If-Match": f'"{version[0]}"'})
    assert second.json()["dictated_on"] == "2024-10-14"
    listed = await client.get(world.url(f"questions/{world.question}/points"))
    assert listed.json()["range_note"]["dictated_on"] == "2024-10-14"


async def save(client: Any, w: World, etag: str, body: str) -> Any:
    payload: dict[str, Any] = {"body": body, "citation_refs": [], "view": None, "provisional": False}
    saved = await client.put(w.url(f"questions/{w.question}/opinion"), json=payload, headers={"If-Match": etag})
    assert saved.status_code == 200, saved.text
    return saved.json()


async def test_each_warning_says_where_its_sentence_sits_in_the_text(client_for: ClientFor, world: World) -> None:
    client = await client_for(world.expert)
    body = "It began well.  Review was due in 3 weeks. Then it ended."
    out = await save(client, world, '"0"', body)
    [issue] = out["lint"]
    assert body[issue["sentence_start"] : issue["sentence_end"]] == "Review was due in 3 weeks."
    workspace = await client.get(world.url(f"questions/{world.question}/workspace"))
    [again] = workspace.json()["opinion"]["lint"]
    assert (again["sentence_start"], again["sentence_end"]) == (issue["sentence_start"], issue["sentence_end"])
