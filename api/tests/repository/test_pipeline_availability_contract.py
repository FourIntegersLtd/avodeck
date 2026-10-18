"""Slice A: the availability read contract (`core/contracts/availability.py`) for slice D.

`evidence_for_case` and `fact_positions` run as the caller under row-level security: only someone
with access to the case sees rows. Evidence is ordered by page number then date, each item with
its position (the smallest word number among its words on the page); a fact's position is the
smallest word number among its words. Storing a page's evidence again replaces it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.contracts import availability
from app.core.db import Database
from app.features.pipeline import repo
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def _item(kind: str, date: str | None, *word_ids: str) -> dict[str, object]:
    return {"kind": kind, "date": date, "date_text": None, "word_ids": list(word_ids)}


async def _store(
    conn: psycopg.AsyncConnection,
    case: uuid.UUID,
    page: uuid.UUID,
    evidence: list[dict[str, object]],
    facts: list[repo.NewFact],
) -> None:
    await repo.store_extraction(
        conn,
        case,
        page,
        source_type=None,
        facts=facts,
        sealed=None,
        needs_human=False,
        reason=None,
        task="T-07b",
        model_id="m",
        prompt_version="v1",
        availability_evidence=evidence,
    )


async def _words(conn: psycopg.AsyncConnection, case: uuid.UUID, page: uuid.UUID, count: int) -> None:
    for n in range(1, count + 1):
        await conn.execute(
            "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence)"
            " values (%s, %s, %s, %s, %s, %s, 0.9)",
            (page, case, n, f"w{n}", b"x", [0, 0, 1, 0, 1, 1, 0, 1]),
        )


def _fact(*word_ids: str) -> repo.NewFact:
    return repo.NewFact(
        kind="k", fields={}, value="v", word_ids=list(word_ids), bbox={"x0": 0, "y0": 0, "x1": 1, "y1": 1}
    )


async def test_the_contract_returns_ordered_evidence_and_positions_only_to_case_members(
    admin: psycopg.Connection, seed: Seed, api_db: Database, worker_db: Database
) -> None:
    expert, stranger = seed.person(), seed.person()
    mine = seed.case(seed.org(members={expert: "owner"}), expert)
    theirs = seed.case(seed.org(members={stranger: "owner"}), stranger)
    doc, other_doc = seed.document(mine), seed.document(theirs)
    first, second, foreign = seed.page(mine, doc, 1), seed.page(mine, doc, 2), seed.page(theirs, other_doc, 1)
    async with worker_db.job(mine) as conn:
        await _words(conn, mine, first, 6)
        await _words(conn, mine, second, 6)
        await _store(
            conn,
            mine,
            second,
            [_item("filed", "2024-04-02", "w4", "w5"), _item("received", "2024-05-14", "w2")],
            [_fact("w6", "w3")],
        )
        await _store(conn, mine, first, [_item("seen", None, "w1")], [_fact("w5")])
    async with worker_db.job(theirs) as conn:
        await _words(conn, theirs, foreign, 2)
        await _store(conn, theirs, foreign, [_item("seen", "2024-01-01", "w1")], [])
    async with api_db.request(scope_for(expert)) as conn:
        got = await availability.evidence_for_case(conn, mine)
        assert [(e.page_no, e.kind, e.date, e.position, e.word_ids) for e in got] == [
            (1, "seen", None, 1, ["w1"]),
            (2, "filed", dt.date(2024, 4, 2), 4, ["w4", "w5"]),
            (2, "received", dt.date(2024, 5, 14), 2, ["w2"]),
        ]
        assert {e.page_id for e in got} == {first, second}
        positions = await availability.fact_positions(conn, mine)
        assert sorted(positions.values()) == [3, 5]
        assert await availability.evidence_for_case(conn, theirs) == []
        assert await availability.fact_positions(conn, theirs) == {}
    async with api_db.request(scope_for(stranger)) as conn:
        assert await availability.evidence_for_case(conn, mine) == []
        assert await availability.fact_positions(conn, mine) == {}


async def test_storing_a_pages_evidence_again_replaces_it(
    admin: psycopg.Connection, seed: Seed, api_db: Database, worker_db: Database
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert)
    page = seed.page(case, seed.document(case), 1)
    async with worker_db.job(case) as conn:
        await _words(conn, case, page, 3)
        await _store(conn, case, page, [_item("seen", None, "w1"), _item("filed", None, "w2")], [])
        await _store(conn, case, page, [_item("received", "2024-05-22", "w3")], [])
    async with api_db.request(scope_for(expert)) as conn:
        got = await availability.evidence_for_case(conn, case)
        assert [(e.kind, e.date) for e in got] == [("received", dt.date(2024, 5, 22))]
