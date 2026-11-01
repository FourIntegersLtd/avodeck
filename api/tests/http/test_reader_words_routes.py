"""Slice B, screen S09: the page's words, for choosing the words a changed fact cites (rule 2, rule 3).

- `GET /v1/cases/{id}/pages/{page_no}/words` sits behind the same blind-read gate as `/facts`: 404 for
  anyone without access to the case, 423 `blind_read` until the person has read the page.
- After the read it returns each word on the page in reading order: `word_id`, `text` (opened from the
  seal) and `box` as a share of the page (0 to 1). Words the expert typed out have no place on the
  picture, so their box is null.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.features.pipeline.paths import word_purpose
from tests.fixtures.stub_facts import LEFT, PAGE_HEIGHT, PAGE_WIDTH, TOP, expected_facts, load_stub_facts
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    from tests.support.files import build_app

    return build_app(api_db, auth_env, crypto=CRYPTO)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def world(seed: Seed, admin: psycopg.Connection, aconn: psycopg.AsyncConnection) -> dict[str, Any]:
    expert, stranger = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case(seed.org(kind="personal", members={stranger: "owner"}), stranger, status="active")
    loaded = await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO)
    page_id = loaded[118].page_id
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id, width, height)"
        " values (%s, (select bundle_id from app.cases where id = %s), %s, %s)"
        " on conflict (page_id) do update set width = excluded.width, height = excluded.height",
        (page_id, case, PAGE_WIDTH, PAGE_HEIGHT),
    )
    return {"case": case, "expert": expert, "stranger": stranger, "page_id": page_id}


def words_url(w: dict[str, Any], n: int = 118) -> str:
    return f"/v1/cases/{w['case']}/pages/{n}/words"


async def test_the_words_stay_hidden_until_the_page_is_read(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    response = await client.get(words_url(world))
    assert response.status_code == 423
    assert response.json()["error"]["code"] == "blind_read"


async def test_someone_without_access_gets_404(client_for: Any, world: dict[str, Any]) -> None:
    stranger = await client_for(world["stranger"])
    assert (await stranger.get(words_url(world))).status_code == 404


async def test_after_the_read_every_word_comes_back_with_its_box(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    assert (
        await client.post(f"/v1/cases/{world['case']}/pages/118/read", json={"dwell_seconds": 9})
    ).status_code == 200
    response = await client.get(words_url(world))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    words = response.json()["words"]
    expected = [word for f in expected_facts([118]) for word in f.quote.split()]
    assert [x["text"] for x in words] == expected
    assert [x["word_id"] for x in words] == [f"p118w{n}" for n in range(1, len(expected) + 1)]
    first = words[0]["box"]
    assert abs(first["x0"] - LEFT / PAGE_WIDTH) < 0.001 and abs(first["y0"] - TOP / PAGE_HEIGHT) < 0.001
    for x in words:
        b = x["box"]
        assert 0 <= b["x0"] < b["x1"] <= 1 and 0 <= b["y0"] < b["y1"] <= 1


async def test_a_typed_out_word_has_no_box(
    admin: psycopg.Connection, aconn: psycopg.AsyncConnection, client_for: Any, world: dict[str, Any]
) -> None:
    page_id: uuid.UUID = world["page_id"]
    sealed = await CRYPTO.encrypt(aconn, world["case"], b"Typed", purpose=word_purpose(page_id, "t1"))
    admin.execute(
        "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
        " values (%s, (select bundle_id from app.cases where id = %s), 9000, 't1', %s, '{0,0,0,0,0,0,0,0}', 1, 'expert')",
        (page_id, world["case"], sealed),
    )
    client = await client_for(world["expert"])
    await client.post(f"/v1/cases/{world['case']}/pages/118/read", json={"dwell_seconds": 9})
    words = (await client.get(words_url(world))).json()["words"]
    assert words[-1] == {"word_id": "t1", "text": "Typed", "box": None}
