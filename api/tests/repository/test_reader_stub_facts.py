"""The stub fact loader (`tests/fixtures/stub_facts.py`) other slices use until they run the pipeline.

It reads the planted bundle's expected facts (`evals/.../expected/facts.json`) and puts the chosen pages
into a case: one page row per bundle page, sealed `ocr_words` for each quote, and one `facts` row per
expected fact citing exactly those words (rule 2). Facts start `suggested`; `verified=True` walks them
to `verified` through the state machine. Values are synthetic (the planted bundle), never real records.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.features.pipeline.paths import word_purpose
from tests.fixtures.stub_facts import expected_facts, load_stub_facts
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    return seed.case(org, expert, status="active")


@pytest.fixture
async def conn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


def test_expected_facts_come_from_the_planted_bundle() -> None:
    every = expected_facts()
    assert len(every) > 1000
    page_118 = expected_facts([118])
    assert page_118 and all(f.page == 118 for f in page_118)
    assert all(f.quote for f in page_118)
    assert expected_facts([999_999]) == []


async def test_page_118_loads_as_suggested_facts_citing_their_own_sealed_words(
    conn: psycopg.AsyncConnection, admin: psycopg.Connection, case: uuid.UUID
) -> None:
    crypto = CaseCrypto(LocalKeyProvider.generate())
    loaded = await load_stub_facts(conn, case, pages=[118], crypto=crypto)

    assert set(loaded) == {118}
    page_id = loaded[118].page_id
    assert admin.execute("select bundle_page_no from app.pages where id = %s", (page_id,)).fetchone() == (118,)
    rows = admin.execute(
        "select id, status, value, word_ids, bbox from app.facts where case_id = %s and page_id = %s",
        (case, page_id),
    ).fetchall()
    assert len(rows) == len(expected_facts([118])) == len(loaded[118].fact_ids)
    assert {r[0] for r in rows} == set(loaded[118].fact_ids)

    words = {
        r[0]: bytes(r[1])
        for r in admin.execute(
            "select word_id, text from app.ocr_words where page_id = %s and source = 'ocr'", (page_id,)
        ).fetchall()
    }
    quotes = {f.quote for f in expected_facts([118])}
    for _fid, status, value, word_ids, bbox in rows:
        assert status == "suggested"
        assert value
        assert set(word_ids) <= set(words), "every cited word exists on the page"
        text = [
            (await crypto.decrypt(conn, case, words[w], purpose=word_purpose(page_id, w))).decode() for w in word_ids
        ]
        assert " ".join(text) in quotes, "the cited words spell the quote"
        assert 0 <= bbox["x0"] < bbox["x1"] <= 1 and 0 <= bbox["y0"] < bbox["y1"] <= 1


async def test_verified_walks_the_facts_to_verified(
    conn: psycopg.AsyncConnection, admin: psycopg.Connection, case: uuid.UUID
) -> None:
    loaded = await load_stub_facts(conn, case, pages=[118, 1], verified=True)
    assert set(loaded) == {1, 118}
    statuses = admin.execute("select distinct status from app.facts where case_id = %s", (case,)).fetchall()
    assert statuses == [("verified",)]


async def test_pages_with_no_expected_facts_load_nothing(
    conn: psycopg.AsyncConnection, admin: psycopg.Connection, case: uuid.UUID
) -> None:
    assert await load_stub_facts(conn, case, pages=[999_999]) == {}
    assert admin.execute("select count(*) from app.facts where case_id = %s", (case,)).fetchone() == (0,)
