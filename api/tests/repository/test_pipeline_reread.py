"""Slice A: reading named pages again (core/contracts/pipeline.py, state machine "Page").

- `process_pages(conn, case_id, page_ids)` reads the named pages again: a turned page (`rotation`) gets
  new pictures and its words read again in the turned frame; it keeps its number. Pages not named are
  not touched, and quarantined pages are left alone.
- `extract_page_text(conn, case_id, page_id)` replaces the page's words with the expert's typed-out
  text (`source = 'expert'`, ids `e1`, `e2`, ...), takes the page off the unreadable queue and sends it
  to the model again.
- Either way the page's unchecked suggestions are superseded (rejected), and each checked fact keeps
  its status: it is re-mapped onto the same words in the new reading, or, when they are gone, flagged
  "Please check" (`fact_flags.flagged_for = 'recheck'`, no person). Job rows carry no page text.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from PIL import Image

from app.core.contracts.pipeline import extract_page_text, process_pages
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from app.features.pipeline import paths
from tests.repository.test_pipeline_support import Harness, empty_answer, harness, page_words, t07b_requests, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.db import scope_for
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PAGES = [["Seen today chest clear"], ["Second page stays"]]
TYPED = "Seen today chest clear review in two weeks"


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def h(
    admin: psycopg.Connection, api_db: Database, worker_dsn: str, auth_env: dict[str, str], seed: Seed, storage: Any
) -> AsyncIterator[Harness]:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    source = default_library().combine(*LAUNCH.codes).source_types()[0]["key"]
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([empty_answer(source)]),
    ) as running:
        await running.upload(text_pdf(PAGES))
        assert set((await running.drain()).values()) == {"done"}
        yield running


def page_ids(h: Harness) -> list[uuid.UUID]:
    return [p["id"] for p in h.pages()]


async def picture_size(h: Harness, page_id: uuid.UUID) -> tuple[int, int]:
    path = paths.page_image_path(h.case_id, page_id)
    image = await h.decrypt(await h.storage.get(path), path)
    return Image.open(io.BytesIO(image)).size  # type: ignore[no-any-return]


def add_fact(h: Harness, page_id: uuid.UUID, value: str, word_ids: list[str], status: str) -> uuid.UUID:
    row = h.admin.execute(
        "insert into app.facts (case_id, page_id, kind, value, word_ids, bbox) values (%s, %s, 'note', %s, %s,"
        " '{\"x0\": 0, \"y0\": 0, \"x1\": 1, \"y1\": 1}') returning id",
        (h.case_id, page_id, value, word_ids),
    ).fetchone()
    assert row is not None
    if status != "suggested":
        h.admin.execute("update app.facts set status = %s where id = %s", (status, row[0]))
    return row[0]  # type: ignore[no-any-return]


def fact(h: Harness, fact_id: uuid.UUID) -> dict[str, Any]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    found = cur.execute("select * from app.facts where id = %s", (fact_id,)).fetchone()
    assert found is not None
    return found


def rechecks(h: Harness) -> list[tuple[Any, ...]]:
    return h.admin.execute(
        "select fact_id, person_id from app.fact_flags where case_id = %s and flagged_for = 'recheck'", (h.case_id,)
    ).fetchall()


async def words_for(h: Harness, page_id: uuid.UUID, wanted: str) -> list[str]:
    words = await page_words(h, page_id)
    want = wanted.split()
    ids = list(words)
    texts = [words[i][0] for i in ids]
    for start in range(len(texts)):
        if texts[start : start + len(want)] == want:
            return ids[start : start + len(want)]
    raise AssertionError("words not on the page")


async def test_a_turned_page_is_read_again_and_keeps_its_number_and_checked_facts(h: Harness) -> None:
    first, second = page_ids(h)
    numbers = {p["id"]: p["bundle_page_no"] for p in h.pages()}
    width, height = await picture_size(h, first)
    other = await picture_size(h, second)
    old_words = await page_words(h, first)
    cited = await words_for(h, first, "chest clear")
    checked = add_fact(h, first, "chest clear", cited, "verified")
    unchecked = add_fact(h, first, "Seen today", await words_for(h, first, "Seen today"), "suggested")
    old_box = fact(h, checked)["bbox"]
    version = fact(h, checked)["version"]
    asked = len(t07b_requests(h))

    h.admin.execute("update app.pages set rotation = 90 where id = %s", (first,))
    async with h.api_db.request(scope_for(h.expert)) as conn:
        job = await process_pages(conn, h.case_id, [first])
    assert set((await h.drain()).values()) == {"done"}

    assert {p["id"]: p["bundle_page_no"] for p in h.pages()} == numbers
    assert await picture_size(h, first) == (height, width), "the picture is turned"
    assert await picture_size(h, second) == other
    assert [t for t, _ in (await page_words(h, first)).values()] == [t for t, _ in old_words.values()]
    size = h.admin.execute("select width, height from app.page_analysis where page_id = %s", (first,)).fetchone()
    assert size is not None and size[0] > size[1], "the page is now landscape"

    kept = fact(h, checked)
    assert kept["status"] == "verified" and kept["version"] > version
    assert [(await page_words(h, first))[w][0] for w in kept["word_ids"]] == ["chest", "clear"]
    assert kept["bbox"] != old_box
    assert fact(h, unchecked)["status"] == "rejected"
    assert rechecks(h) == []
    assert len(t07b_requests(h)) == asked + 1, "only the turned page is sent to the model again"
    row = h.admin.execute("select row_to_json(j)::text from app.jobs j where id = %s", (job,)).fetchone()
    assert row is not None and "chest" not in row[0]


async def test_the_experts_text_replaces_the_words_and_is_sent_to_the_model(h: Harness) -> None:
    first, _ = page_ids(h)
    kept_id = add_fact(h, first, "chest clear", await words_for(h, first, "chest clear"), "verified")
    lost_id = add_fact(h, first, "Seen", await words_for(h, first, "Seen"), "edited")
    h.admin.execute(
        "update app.pages set unreadable = true, transcribed_by_expert_text = %s where id = %s", (TYPED, first)
    )
    async with h.api_db.request(scope_for(h.expert)) as conn:
        await extract_page_text(conn, h.case_id, first)
    h.admin.execute("update app.pages set transcribed_by_expert_text = %s where id = %s", ("chest clear", first))
    async with h.api_db.request(scope_for(h.expert)) as conn:
        await extract_page_text(conn, h.case_id, first)
    assert set((await h.drain()).values()) == {"done"}

    words = await page_words(h, first)
    assert list(words) == ["e1", "e2"] and [t for t, _ in words.values()] == ["chest", "clear"]
    sources = h.admin.execute("select distinct source from app.ocr_words where page_id = %s", (first,)).fetchall()
    assert sources == [("expert",)]
    page = next(p for p in h.pages() if p["id"] == first)
    assert (page["text_source"], page["unreadable"]) == ("expert", False)

    assert (fact(h, kept_id)["status"], fact(h, kept_id)["word_ids"]) == ("verified", ["e1", "e2"])
    assert fact(h, lost_id)["status"] == "edited"
    assert rechecks(h) == [(lost_id, None)]
    assert t07b_requests(h)[-1].text.split() == ["e1", "chest", "e2", "clear"], "the model reads the typed words"


async def test_quarantined_pages_are_not_read_again(h: Harness) -> None:
    first, _ = page_ids(h)
    h.admin.execute("update app.pages set quarantined = true, rotation = 180 where id = %s", (first,))
    size = await picture_size(h, first)
    asked = len(t07b_requests(h))
    async with h.api_db.request(scope_for(h.expert)) as conn:
        await process_pages(conn, h.case_id, [first])
    await h.drain()
    assert await picture_size(h, first) == size
    assert len(t07b_requests(h)) == asked
