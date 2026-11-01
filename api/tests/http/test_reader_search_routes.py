"""Slice B, screen S09: search the bundle's words (approved 2026-10-23, page viewer item 6).

- `POST /v1/cases/{id}/search` `{q}` searches the words read from the pages (`ocr_words`, through the
  `document_words` contract, so the expert's own readings of misread words count) and nothing else:
  never the suggestions or facts, so the blind read stays intact (rule 3). Searching is not reading.
- It answers the page numbers with hits, how many on each, and each hit's box as a share of the page
  so the screen can mark it. It never answers the words themselves (the page picture shows them).
- Matching ignores case, spacing and punctuation; several words must stand next to each other.
- Pages set aside (quarantined) are not searched.
- The search words travel in the body, not the address, and are never logged (rule 6). Never cached.
- Who may search: anyone allowed `case.view_content`; anyone without access gets 404; without CSRF 403.
  Fewer than two letters or numbers is 422.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.features.pipeline.paths import word_purpose
from app.features.reader import repo
from tests.support.db import scope_for
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
WIDTH, HEIGHT = 1000.0, 1400.0


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    return build_app(api_db, auth_env, crypto=CRYPTO)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


async def add_page(
    seed: Seed, aconn: psycopg.AsyncConnection, case: uuid.UUID, document: uuid.UUID, number: int, words: list[str]
) -> uuid.UUID:
    """A page whose words read `words`, one line, each word 100 wide and 20 high from the top left."""
    page = seed.page(case, document, number)
    await aconn.execute(
        "insert into app.page_analysis (page_id, bundle_id, width, height)"
        " values (%s, (select bundle_id from app.cases where id = %s), %s, %s)",
        (page, case, WIDTH, HEIGHT),
    )
    for n, text in enumerate(words, start=1):
        wid = f"w{n}"
        x0, x1 = 100.0 * (n - 1), 100.0 * n
        sealed = await CRYPTO.encrypt(aconn, case, text.encode(), purpose=word_purpose(page, wid))
        await aconn.execute(
            "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
            " values (%s, (select bundle_id from app.cases where id = %s), %s, %s, %s, %s::real[], 0.99, 'ocr')",
            (page, case, n, wid, sealed, [x0, 0.0, x1, 0.0, x1, 20.0, x0, 20.0]),
        )
    return page


@pytest.fixture
async def world(seed: Seed, aconn: psycopg.AsyncConnection) -> dict[str, Any]:
    expert, viewer, stranger = seed.person(), seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case_member(case, viewer, "viewer", added_by=expert)
    seed.case(seed.org(kind="personal", members={stranger: "owner"}), stranger, status="active")
    doc = seed.document(case)
    pages = {
        1: await add_page(seed, aconn, case, doc, 1, ["Started", "METFORMIN", "500mg", "daily."]),
        2: await add_page(seed, aconn, case, doc, 2, ["Blood", "pressure", "high;", "metformin", "stopped"]),
        3: await add_page(seed, aconn, case, doc, 3, ["Pressure", "of", "blood", "normal"]),
        4: await add_page(seed, aconn, case, seed.document(case), 4, ["Seen", "with", "chest", "pain"]),
    }
    return {"case": case, "expert": expert, "viewer": viewer, "stranger": stranger, "pages": pages}


def base(w: dict[str, Any]) -> str:
    return f"/v1/cases/{w['case']}"


async def search(client: httpx.AsyncClient, w: dict[str, Any], q: Any) -> httpx.Response:
    return await client.post(f"{base(w)}/search", json={"q": q})


def found(response: httpx.Response) -> dict[int, int]:
    assert response.status_code == 200, response.text
    return {p["page_no"]: p["hits"] for p in response.json()["pages"]}


async def test_lists_the_pages_with_hits_ignoring_case_and_punctuation(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    assert found(await search(client, world, "metformin")) == {1: 1, 2: 1}
    assert found(await search(client, world, "  Daily ")) == {1: 1}
    assert found(await search(client, world, "HIGH")) == {2: 1}
    response = await search(client, world, "metformin")
    assert response.json()["total_hits"] == 2
    assert response.headers["cache-control"] == "private, no-store"


async def test_several_words_must_stand_next_to_each_other(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    assert found(await search(client, world, "blood pressure")) == {2: 1}
    assert found(await search(client, world, "pressure of blood")) == {3: 1}
    assert found(await search(client, world, "chest metformin")) == {}


async def test_each_hit_has_its_box_on_the_page_and_no_words_come_back(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    response = await search(client, world, "blood pressure")
    (page,) = response.json()["pages"]
    assert page["boxes"] == [{"x0": 0.0, "y0": 0.0, "x1": 0.2, "y1": pytest.approx(20 / HEIGHT, abs=1e-5)}]
    body = response.text.lower()
    for word in ("blood", "pressure", "high", "stopped"):
        assert word not in body


async def test_the_experts_own_reading_of_a_misread_word_is_searched(
    api_db: Database, aconn: psycopg.AsyncConnection, seed: Seed, client_for: Any, world: dict[str, Any]
) -> None:
    page = await add_page(seed, aconn, world["case"], seed.document(world["case"]), 5, ["Amoxicilin", "250"])
    async with api_db.request(scope_for(world["expert"])) as conn:
        await repo.save_correction(conn, CRYPTO, world["case"], page, ["w1"], "Amoxicillin", world["expert"])
    client = await client_for(world["expert"])
    assert found(await search(client, world, "amoxicillin")) == {5: 1}
    assert found(await search(client, world, "amoxicilin 250")) == {}


async def test_searching_is_not_reading_and_never_shows_suggestions(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = await client_for(world["expert"])
    assert found(await search(client, world, "metformin")) == {1: 1, 2: 1}
    facts = await client.get(f"{base(world)}/pages/1/facts")
    assert facts.status_code == 423 and facts.json()["error"]["code"] == "blind_read"
    reads = admin.execute("select count(*) from app.page_reads where person_id = %s", (world["expert"],)).fetchone()
    assert reads == (0,)


async def test_pages_set_aside_are_not_searched(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    admin.execute("update app.pages set quarantined = true where id = %s", (world["pages"][2],))
    client = await client_for(world["expert"])
    assert found(await search(client, world, "metformin")) == {1: 1}


@pytest.mark.parametrize("q", ["", " ", "a", "-", "x" * 101])
async def test_too_little_or_too_much_to_search_for_is_refused(client_for: Any, world: dict[str, Any], q: str) -> None:
    client = await client_for(world["expert"])
    assert (await search(client, world, q)).status_code == 422


async def test_who_may_search(client_for: Any, world: dict[str, Any]) -> None:
    assert found(await search(await client_for(world["viewer"]), world, "chest")) == {4: 1}
    assert (await search(await client_for(world["stranger"]), world, "chest")).status_code == 404
    assert (await search(await client_for(world["expert"], csrf=False), world, "chest")).status_code == 403


async def test_the_search_words_are_never_logged(
    client_for: Any, world: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    client = await client_for(world["expert"])
    with caplog.at_level(logging.DEBUG):
        assert found(await search(client, world, "chest pain")) == {4: 1}
    assert "chest" not in caplog.text.lower()
