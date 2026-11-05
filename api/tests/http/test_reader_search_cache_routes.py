"""Bundle search with the words cached per case (scalability PR 10).

Only how the words are loaded changes, never what is returned: the same answers, the same blind-read and
access rules, and no patient text in logs. The words are opened once per case, in a thread, and kept for a
short time; they are opened again when pages are added or the expert's readings change, and when the cache is
cleared for a case (PR 15 does that when a case key is destroyed).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.contracts import document_words
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.features.pipeline.paths import word_purpose
from app.features.reader import repo, search_cache
from tests.support.db import scope_for
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
WIDTH, HEIGHT = 1000.0, 1400.0
FIXTURE_WORDS = 17  # the words on the four fixture pages


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    search_cache.clear_all()
    yield
    search_cache.clear_all()


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """One entry per word opened from the seal (the AES step of every word the search loads)."""
    calls: list[int] = []
    real = document_words._open_word
    counter = itertools.count()

    def spy(*args: Any) -> Any:
        calls.append(next(counter))
        return real(*args)

    monkeypatch.setattr(document_words, "_open_word", spy)
    return calls


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


async def search(client: httpx.AsyncClient, w: dict[str, Any], q: str) -> httpx.Response:
    return await client.post(f"{base(w)}/search", json={"q": q})


def found(response: httpx.Response) -> dict[int, int]:
    assert response.status_code == 200, response.text
    return {p["page_no"]: p["hits"] for p in response.json()["pages"]}


async def test_two_searches_on_one_case_open_the_words_once(
    client_for: Any, world: dict[str, Any], opened: list[int]
) -> None:
    client = await client_for(world["expert"])
    assert found(await search(client, world, "metformin")) == {1: 1, 2: 1}
    assert len(opened) == FIXTURE_WORDS
    assert found(await search(client, world, "blood pressure")) == {2: 1}
    assert found(await search(client, world, "chest")) == {4: 1}
    assert len(opened) == FIXTURE_WORDS  # nothing opened again


async def test_another_person_with_access_is_served_from_the_same_words(
    client_for: Any, world: dict[str, Any], opened: list[int]
) -> None:
    assert found(await search(await client_for(world["expert"]), world, "chest")) == {4: 1}
    assert found(await search(await client_for(world["viewer"]), world, "chest")) == {4: 1}
    assert len(opened) == FIXTURE_WORDS


async def test_the_answer_is_the_same_from_the_cache_as_from_a_fresh_load(
    client_for: Any, world: dict[str, Any]
) -> None:
    client = await client_for(world["expert"])
    fresh = [(await search(client, world, q)).json() for q in ("metformin", "blood pressure", "pressure of blood")]
    cached = [(await search(client, world, q)).json() for q in ("metformin", "blood pressure", "pressure of blood")]
    assert fresh == cached
    assert fresh[1]["pages"][0]["boxes"] == [{"x0": 0.0, "y0": 0.0, "x1": 0.2, "y1": pytest.approx(20 / HEIGHT, abs=1e-5)}]


async def test_a_page_added_to_the_case_is_searched_at_once(
    client_for: Any,
    seed: Seed,
    aconn: psycopg.AsyncConnection,
    world: dict[str, Any],
    opened: list[int],
) -> None:
    client = await client_for(world["expert"])
    assert found(await search(client, world, "amoxicillin")) == {}
    await add_page(seed, aconn, world["case"], seed.document(world["case"]), 5, ["Amoxicillin", "250mg"])
    assert found(await search(client, world, "amoxicillin")) == {5: 1}
    assert len(opened) == FIXTURE_WORDS + FIXTURE_WORDS + 2  # loaded again, with the new page


async def test_a_new_reading_by_the_expert_is_searched_at_once(
    api_db: Database, client_for: Any, world: dict[str, Any]
) -> None:
    client = await client_for(world["expert"])
    assert found(await search(client, world, "metformin")) == {1: 1, 2: 1}
    async with api_db.request(scope_for(world["expert"])) as conn:
        await repo.save_correction(conn, CRYPTO, world["case"], world["pages"][4], ["w1"], "Metformin", world["expert"])
    assert found(await search(client, world, "metformin")) == {1: 1, 2: 1, 4: 1}


async def test_pages_set_aside_after_the_words_were_cached_are_not_searched(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = await client_for(world["expert"])
    assert found(await search(client, world, "metformin")) == {1: 1, 2: 1}
    admin.execute("update app.pages set quarantined = true where id = %s", (world["pages"][2],))
    assert found(await search(client, world, "metformin")) == {1: 1}


async def test_clearing_the_case_makes_the_next_search_open_the_words_again(
    client_for: Any, world: dict[str, Any], opened: list[int]
) -> None:
    client = await client_for(world["expert"])
    await search(client, world, "metformin")
    search_cache.clear_case(world["case"])
    await search(client, world, "metformin")
    assert len(opened) == 2 * FIXTURE_WORDS


async def test_the_cache_only_holds_the_set_number_of_cases(
    client_for: Any, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_cache, "_cache", search_cache.SearchCache(max_cases=1, ttl_seconds=600.0, max_words=10**6))
    client = await client_for(world["expert"])
    await search(client, world, "metformin")
    assert search_cache.case_ids() == [world["case"]]


async def test_somebody_without_access_gets_nothing_from_a_warm_cache(
    client_for: Any, world: dict[str, Any], opened: list[int]
) -> None:
    await search(await client_for(world["expert"]), world, "chest")
    assert (await search(await client_for(world["stranger"]), world, "chest")).status_code == 404
    assert (await search(await client_for(world["expert"], csrf=False), world, "chest")).status_code == 403
    assert len(opened) == FIXTURE_WORDS


async def test_searching_is_still_not_reading_with_a_warm_cache(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = await client_for(world["expert"])
    await search(client, world, "metformin")
    await search(client, world, "metformin")
    facts = await client.get(f"{base(world)}/pages/1/facts")
    assert facts.status_code == 423 and facts.json()["error"]["code"] == "blind_read"
    reads = admin.execute("select count(*) from app.page_reads where person_id = %s", (world["expert"],)).fetchone()
    assert reads == (0,)


async def test_the_search_words_and_page_words_are_never_logged(
    client_for: Any, world: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    client = await client_for(world["expert"])
    with caplog.at_level(logging.DEBUG):
        await search(client, world, "chest pain")
        await search(client, world, "chest pain")
    text = caplog.text.lower()
    for word in ("chest", "pain", "metformin", "blood"):
        assert word not in text


async def test_a_search_of_a_4000_page_bundle_does_not_hold_up_other_requests(
    seed: Seed, aconn: psycopg.AsyncConnection, client_for: Any, world: dict[str, Any]
) -> None:
    case = world["case"]
    doc = seed.document(case)
    bundle = (await (await aconn.execute("select bundle_id from app.cases where id = %s", (case,))).fetchone())[0]  # type: ignore[index]
    ids = [uuid.uuid4() for _ in range(4000)]
    await aconn.cursor().executemany(
        "insert into app.pages (id, bundle_id, document_id, bundle_page_no) values (%s, %s, %s, %s)",
        [(p, bundle, doc, 100 + i) for i, p in enumerate(ids)],
    )
    rows = []
    for page in ids:
        for n in range(1, 26):
            wid = f"w{n}"
            sealed = await CRYPTO.encrypt(aconn, case, b"routine" if n % 5 else b"warfarin", purpose=word_purpose(page, wid))
            x0 = 30.0 * (n - 1)
            rows.append((page, bundle, n, wid, sealed, [x0, 0.0, x0 + 30, 0.0, x0 + 30, 20.0, x0, 20.0]))
    await aconn.cursor().executemany(
        "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
        " values (%s, %s, %s, %s, %s, %s::real[], 0.99, 'ocr')",
        rows,
    )
    client = await client_for(world["expert"])
    lags: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        while not stop.is_set():
            before = time.perf_counter()
            await asyncio.sleep(0.01)
            lags.append(time.perf_counter() - before - 0.01)

    watcher = asyncio.create_task(ticker())
    try:
        first = await search(client, world, "warfarin")  # opens 100,000 words
        second = await search(client, world, "warfarin")
    finally:
        stop.set()
        await watcher
    assert found(first) == found(second) and len(found(first)) == 4000
    lags.sort()
    assert lags[int(len(lags) * 0.99) - 1] < 0.1
