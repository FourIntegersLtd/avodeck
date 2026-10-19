"""Slice B, screen S09: the page routes (architecture 5, 09 Reader; rule 3).

- `GET /v1/cases/{id}/pages`: the pages in order, each with the person's own read state, the
  "in the period" flag and its source; `filter=unread|period|source`, `cursor` and `limit`.
  Pages set aside (quarantined) are not listed.
- `GET /v1/cases/{id}/pages/{n}`: one page. It never carries facts.
- `GET /v1/cases/{id}/pages/{n}/image`: the page picture, never cached.
- `POST /v1/cases/{id}/pages/{n}/read` `{dwell_seconds}`: records the read and unlocks the suggestions.
- `GET /v1/cases/{id}/pages/{n}/facts`: 423 `blind_read` until the person has read the page, then the
  facts with the words they cite and a box around them. A page set aside as a copy shows none (they
  show on the original).
- `GET /v1/cases/{id}/progress`: pages read out of the total and a time estimate from the person's own
  median seconds per page for that kind of source (20 seconds a page before any read).

Anyone without access to the case gets 404 on every route.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.storage import SupabaseStorage
from app.features.pipeline.paths import page_image_path
from tests.fixtures.stub_facts import load_stub_facts
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
def world(seed: Seed) -> dict[str, Any]:
    expert, stranger = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case(seed.org(kind="personal", members={stranger: "owner"}), stranger, status="active")
    return {"case": case, "expert": expert, "stranger": stranger}


def base(w: dict[str, Any]) -> str:
    return f"/v1/cases/{w['case']}"


async def mark_read(client: httpx.AsyncClient, w: dict[str, Any], n: int, dwell: int = 30) -> httpx.Response:
    return await client.post(f"{base(w)}/pages/{n}/read", json={"dwell_seconds": dwell})


async def test_suggestions_stay_hidden_until_the_page_is_read(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    loaded = await load_stub_facts(aconn, world["case"], pages=[118], crypto=CRYPTO)
    client = await client_for(world["expert"])

    hidden = await client.get(f"{base(world)}/pages/118/facts")
    assert hidden.status_code == 423 and hidden.json()["error"]["code"] == "blind_read"

    read = await mark_read(client, world, 118)
    assert read.status_code == 200 and read.json()["read"] is True

    shown = await client.get(f"{base(world)}/pages/118/facts")
    assert shown.status_code == 200
    facts = shown.json()["facts"]
    assert {uuid.UUID(f["id"]) for f in facts} == set(loaded[118].fact_ids)
    assert all(f["word_ids"] and f["bbox"] and f["status"] == "suggested" and f["version"] >= 1 for f in facts)


async def test_the_page_itself_never_carries_facts(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    await load_stub_facts(aconn, world["case"], pages=[118], crypto=CRYPTO)
    client = await client_for(world["expert"])

    before = await client.get(f"{base(world)}/pages/118")
    assert before.status_code == 200
    page = before.json()
    assert page["page_no"] == 118 and page["read"] is False and "facts" not in page
    await mark_read(client, world, 118)
    after = (await client.get(f"{base(world)}/pages/118")).json()
    assert after["read"] is True and after["first_read_at"] and "facts" not in after


async def test_a_page_dwell_outside_a_day_is_refused(seed: Seed, client_for: Any, world: dict[str, Any]) -> None:
    seed.page(world["case"], seed.document(world["case"]), 1)
    client = await client_for(world["expert"])
    assert (await mark_read(client, world, 1, dwell=-1)).status_code == 422
    assert (await mark_read(client, world, 1, dwell=86_401)).status_code == 422


async def test_a_copy_shows_no_suggestions_they_show_on_the_original(
    client_for: Any, aconn: psycopg.AsyncConnection, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    loaded = await load_stub_facts(aconn, world["case"], pages=[118, 1], crypto=CRYPTO)
    admin.execute(
        "update app.pages set duplicate_of_page_id = %s where id = %s", (loaded[118].page_id, loaded[1].page_id)
    )
    client = await client_for(world["expert"])
    await mark_read(client, world, 1)
    assert (await client.get(f"{base(world)}/pages/1/facts")).json()["facts"] == []
    assert (await client.get(f"{base(world)}/pages/1")).json()["duplicate_of_page_no"] == 118


async def test_a_page_set_aside_is_not_shown(
    seed: Seed, client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    doc = seed.document(world["case"])
    seed.page(world["case"], doc, 1)
    aside = seed.page(world["case"], doc, 2)
    admin.execute("update app.pages set quarantined = true where id = %s", (aside,))
    client = await client_for(world["expert"])
    assert (await client.get(f"{base(world)}/pages/2")).status_code == 404
    assert (await mark_read(client, world, 2)).status_code == 404
    assert [p["page_no"] for p in (await client.get(f"{base(world)}/pages")).json()["pages"]] == [1]


async def test_the_page_picture_is_streamed_and_never_cached(
    seed: Seed,
    client_for: Any,
    aconn: psycopg.AsyncConnection,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    world: dict[str, Any],
) -> None:
    page = seed.page(world["case"], seed.document(world["case"]), 1)
    path = page_image_path(world["case"], page)
    admin.execute("update app.pages set image_path = %s where id = %s", (path, page))
    picture = b"\xff\xd8\xff synthetic picture"
    await storage.put(path, await CRYPTO.encrypt(aconn, world["case"], picture, purpose=path))

    response = await (await client_for(world["expert"])).get(f"{base(world)}/pages/1/image")
    assert response.status_code == 200
    assert response.content == picture
    assert response.headers["cache-control"] == "private, no-store"


async def test_the_list_filters_by_unread_period_and_source(
    seed: Seed, client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    case = world["case"]
    doc = seed.document(case)
    pages = {n: seed.page(case, doc, n) for n in (1, 2, 3, 4)}
    admin.execute(
        "update app.cases set period_start = %s, period_end = %s where id = %s",
        (date(2024, 1, 1), date(2024, 12, 31), case),
    )
    for n, source, printed in (
        (1, "letter", date(2023, 5, 1)),
        (2, "letter", date(2024, 6, 1)),
        (3, "result", date(2024, 7, 1)),
        (4, "result", None),
    ):
        admin.execute("update app.pages set source_type = %s where id = %s", (source, pages[n]))
        admin.execute(
            "insert into app.page_analysis (page_id, case_id, printed_dates) values (%s, %s, %s)",
            (pages[n], case, [printed] if printed else []),
        )
    client = await client_for(world["expert"])
    await mark_read(client, world, 2)

    def numbers(r: httpx.Response) -> list[int]:
        assert r.status_code == 200
        return [p["page_no"] for p in r.json()["pages"]]

    every = await client.get(f"{base(world)}/pages")
    assert numbers(every) == [1, 2, 3, 4]
    assert [(p["read"], p["in_period"], p["source_type"]) for p in every.json()["pages"]] == [
        (False, False, "letter"),
        (True, True, "letter"),
        (False, True, "result"),
        (False, False, "result"),
    ]
    assert numbers(await client.get(f"{base(world)}/pages", params={"filter": "unread"})) == [1, 3, 4]
    assert numbers(await client.get(f"{base(world)}/pages", params={"filter": "period"})) == [2, 3]
    assert numbers(await client.get(f"{base(world)}/pages", params={"filter": "source", "source": "result"})) == [3, 4]

    first = await client.get(f"{base(world)}/pages", params={"limit": 3})
    assert numbers(first) == [1, 2, 3] and first.json()["next_cursor"] == 3
    rest = await client.get(f"{base(world)}/pages", params={"limit": 3, "cursor": 3})
    assert numbers(rest) == [4] and rest.json()["next_cursor"] is None


async def test_progress_counts_reads_and_estimates_from_the_persons_own_pace(
    seed: Seed, client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    case = world["case"]
    doc = seed.document(case)
    for n in (1, 2, 3):
        admin.execute("update app.pages set source_type = 'letter' where id = %s", (seed.page(case, doc, n),))
    client = await client_for(world["expert"])

    assert (await client.get(f"{base(world)}/progress")).json() == {"read": 0, "total": 3, "seconds_left": 60}
    await mark_read(client, world, 1, dwell=50)
    await mark_read(client, world, 1, dwell=10)  # a second look at the same page counts once
    assert (await client.get(f"{base(world)}/progress")).json() == {"read": 1, "total": 3, "seconds_left": 2 * 30}


async def test_a_stranger_gets_404_on_every_route(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    await load_stub_facts(aconn, world["case"], pages=[118], crypto=CRYPTO)
    expert = await client_for(world["expert"])
    await mark_read(expert, world, 118)
    stranger = await client_for(world["stranger"])
    for method, path, body in (
        ("GET", "/pages", None),
        ("GET", "/pages/118", None),
        ("GET", "/pages/118/image", None),
        ("POST", "/pages/118/read", {"dwell_seconds": 5}),
        ("GET", "/pages/118/facts", None),
        ("GET", "/progress", None),
    ):
        response = await stranger.request(method, base(world) + path, json=body)
        assert response.status_code == 404, (method, path)


async def test_a_page_carries_the_period_in_question(
    seed: Seed, client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    """S09's banner names the period ("Feb to Aug 2024"): the page carries the case's start and end, or none."""
    case = world["case"]
    seed.page(case, seed.document(case), 1)
    client = await client_for(world["expert"])
    none = (await client.get(f"{base(world)}/pages/1")).json()
    assert (none["period_start"], none["period_end"]) == (None, None)
    admin.execute(
        "update app.cases set period_start = %s, period_end = %s where id = %s",
        (date(2024, 2, 1), date(2024, 8, 31), case),
    )
    got = (await client.get(f"{base(world)}/pages/1")).json()
    assert (got["period_start"], got["period_end"]) == ("2024-02-01", "2024-08-31")
