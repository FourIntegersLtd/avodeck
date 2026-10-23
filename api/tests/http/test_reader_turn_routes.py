"""Slice B, screen S09: turning a page for reading, at once (approved 2026-10-23, page viewer item 1).

- `PUT /v1/cases/{id}/pages/{n}/turn` `{degrees: 0|90|180|270}` stores how the page is turned for
  reading (clockwise, absolute, so "undo" puts the previous value back). It answers the page at once.
- Nothing about the record changes: the stored picture, `pages.rotation`, the words, their boxes and
  every fact's words and box stay as they were, and no job runs (no reading again, no `process_pages`).
  The screen turns the picture and everything drawn on it together, so provenance (rule 2), the word
  picker and the highlights still land on the same words after 90, 180, 270 and undo.
- The page detail and the page list carry `turn`; turning is not reading (rule 3: facts stay 423).
- Who may turn: `page.rotate` (the named expert and an assistant); a viewer gets 403; anyone without
  access gets 404; without CSRF 403. A turn outside 0/90/180/270 is 422.
- `GET /pages/{n}/image?size=small` is a small picture of the page for the page strip, never cached.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from PIL import Image

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.storage import SupabaseStorage
from app.features.pipeline.paths import page_image_path
from tests.fixtures.stub_facts import PAGE_HEIGHT, PAGE_WIDTH, load_stub_facts
from tests.support.files import build_app, local_storage
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: SupabaseStorage) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=CRYPTO)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def world(seed: Seed, admin: psycopg.Connection, aconn: psycopg.AsyncConnection) -> dict[str, Any]:
    expert, assistant, viewer, stranger = seed.person(), seed.person(), seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    seed.case_member(case, viewer, "viewer", added_by=expert)
    seed.case(seed.org(kind="personal", members={stranger: "owner"}), stranger, status="active")
    loaded = await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO)
    page_id = loaded[118].page_id
    admin.execute(
        "insert into app.page_analysis (page_id, case_id, width, height) values (%s, %s, %s, %s)"
        " on conflict (page_id) do update set width = excluded.width, height = excluded.height",
        (page_id, case, PAGE_WIDTH, PAGE_HEIGHT),
    )
    return {
        "case": case,
        "expert": expert,
        "assistant": assistant,
        "viewer": viewer,
        "stranger": stranger,
        "page_id": page_id,
    }


def base(w: dict[str, Any]) -> str:
    return f"/v1/cases/{w['case']}"


async def turn(client: httpx.AsyncClient, w: dict[str, Any], degrees: Any, n: int = 118) -> httpx.Response:
    return await client.put(f"{base(w)}/pages/{n}/turn", json={"degrees": degrees})


def record(admin: psycopg.Connection, w: dict[str, Any]) -> dict[str, Any]:
    """Everything the turn must leave alone: the page row, its words and boxes, its facts, the case's jobs."""
    page = admin.execute(
        "select rotation, image_path from app.pages where id = %s", (w["page_id"],)
    ).fetchone()
    words = admin.execute(
        "select word_id, polygon, text from app.ocr_words where page_id = %s order by n", (w["page_id"],)
    ).fetchall()
    facts = admin.execute(
        "select id, word_ids, bbox::text, status, version from app.facts where page_id = %s order by id",
        (w["page_id"],),
    ).fetchall()
    jobs = admin.execute("select count(*) from app.jobs where case_id = %s", (w["case"],)).fetchone()
    return {"page": page, "words": words, "facts": facts, "jobs": jobs}


async def test_a_turn_is_stored_and_shown_at_once_without_reading_the_page_again(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = await client_for(world["expert"])
    before = record(admin, world)

    response = await turn(client, world, 180)
    assert response.status_code == 200
    assert response.json()["page_no"] == 118 and response.json()["turn"] == 180

    assert (await client.get(f"{base(world)}/pages/118")).json()["turn"] == 180
    listed = (await client.get(f"{base(world)}/pages")).json()["pages"]
    assert [p["turn"] for p in listed if p["page_no"] == 118] == [180]
    assert record(admin, world) == before  # no new picture, words, boxes, facts or job


async def test_every_turn_and_undo_keeps_the_words_their_boxes_and_the_facts_exactly(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = await client_for(world["expert"])
    assert (await client.post(f"{base(world)}/pages/118/read", json={"dwell_seconds": 9})).status_code == 200
    words = (await client.get(f"{base(world)}/pages/118/words")).json()
    facts = (await client.get(f"{base(world)}/pages/118/facts")).json()["facts"]
    assert facts and all(f["bbox"] for f in facts)

    for degrees in (90, 180, 270, 0, 270):  # 0 is "undo" back to upright
        assert (await turn(client, world, degrees)).json()["turn"] == degrees
        assert (await client.get(f"{base(world)}/pages/118/words")).json() == words
        assert (await client.get(f"{base(world)}/pages/118/facts")).json()["facts"] == facts


async def test_after_a_turn_a_fact_is_still_checked_against_the_words_it_cites(
    client_for: Any, world: dict[str, Any]
) -> None:
    """Rule 2 after a turn: re-citing a fact's own words for its own value passes; other words do not."""
    client = await client_for(world["expert"])
    await client.post(f"{base(world)}/pages/118/read", json={"dwell_seconds": 9})
    fact = next(f for f in (await client.get(f"{base(world)}/pages/118/facts")).json()["facts"] if f["value"])
    others = [
        w["word_id"]
        for w in (await client.get(f"{base(world)}/pages/118/words")).json()["words"]
        if w["word_id"] not in fact["word_ids"]
    ][:2]
    await turn(client, world, 90)

    wrong = await client.post(
        f"{base(world)}/facts/{fact['id']}/verify",
        json={"action": "edit", "new_value": fact["value"], "word_ids": others},
        headers={"If-Match": f'"{fact["version"]}"'},
    )
    assert wrong.status_code == 422
    right = await client.post(
        f"{base(world)}/facts/{fact['id']}/verify",
        json={"action": "edit", "new_value": fact["value"], "word_ids": fact["word_ids"]},
        headers={"If-Match": f'"{fact["version"]}"'},
    )
    assert right.status_code == 200
    assert right.json()["word_ids"] == fact["word_ids"] and right.json()["bbox"] == fact["bbox"]


async def test_turning_is_not_reading(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    await turn(client, world, 90)
    facts = await client.get(f"{base(world)}/pages/118/facts")
    assert facts.status_code == 423 and facts.json()["error"]["code"] == "blind_read"
    assert (await client.get(f"{base(world)}/pages/118")).json()["read"] is False


async def test_a_page_never_turned_is_upright(seed: Seed, client_for: Any, world: dict[str, Any]) -> None:
    seed.page(world["case"], seed.document(world["case"]), 1)
    client = await client_for(world["expert"])
    assert (await client.get(f"{base(world)}/pages/1")).json()["turn"] == 0
    assert {p["turn"] for p in (await client.get(f"{base(world)}/pages")).json()["pages"]} == {0}


@pytest.mark.parametrize("degrees", [45, -90, 360, "upside"])
async def test_only_quarter_turns_are_allowed(client_for: Any, world: dict[str, Any], degrees: Any) -> None:
    client = await client_for(world["expert"])
    assert (await turn(client, world, degrees)).status_code == 422


async def test_the_named_expert_and_an_assistant_turn_a_viewer_cannot_and_a_stranger_sees_nothing(
    client_for: Any, world: dict[str, Any]
) -> None:
    assert (await turn(await client_for(world["assistant"]), world, 270)).status_code == 200
    # The turn is the page's, so the named expert sees what the assistant set.
    expert = await client_for(world["expert"])
    assert (await expert.get(f"{base(world)}/pages/118")).json()["turn"] == 270
    assert (await turn(await client_for(world["viewer"]), world, 90)).status_code == 403
    assert (await turn(await client_for(world["stranger"]), world, 90)).status_code == 404
    assert (await turn(expert, world, 90, n=9999)).status_code == 404


async def test_a_turn_needs_csrf(client_for: Any, world: dict[str, Any]) -> None:
    bare = await client_for(world["expert"], csrf=False)
    assert (await turn(bare, world, 90)).status_code == 403


async def test_the_small_picture_for_the_page_strip_is_small_and_never_cached(
    client_for: Any,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: SupabaseStorage,
    world: dict[str, Any],
) -> None:
    path = page_image_path(world["case"], world["page_id"])
    admin.execute("update app.pages set image_path = %s where id = %s", (path, world["page_id"]))
    out = io.BytesIO()
    Image.new("RGB", (1000, 1400), "white").save(out, "JPEG")
    await storage.put(path, await CRYPTO.encrypt(aconn, world["case"], out.getvalue(), purpose=path))
    client = await client_for(world["expert"])

    small = await client.get(f"{base(world)}/pages/118/image", params={"size": "small"})
    assert small.status_code == 200
    assert small.headers["cache-control"] == "private, no-store"
    assert small.headers["content-type"] == "image/jpeg"
    picture = Image.open(io.BytesIO(small.content))
    assert picture.width <= 240 and abs(picture.height / picture.width - 1.4) < 0.02

    full = await client.get(f"{base(world)}/pages/118/image")
    assert Image.open(io.BytesIO(full.content)).size == (1000, 1400)
    assert (await client.get(f"{base(world)}/pages/118/image", params={"size": "huge"})).status_code == 422
    stranger = await client_for(world["stranger"])
    assert (await stranger.get(f"{base(world)}/pages/118/image", params={"size": "small"})).status_code == 404
