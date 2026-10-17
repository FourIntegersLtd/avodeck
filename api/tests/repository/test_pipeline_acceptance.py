"""Slice A acceptance: the whole 900-page synthetic bundle, processed once for this module (slow).

Run with `RUN_SLOW=1` (or `-m slow` with it set). The bundle and its recorded OCR and model answers
come from the generator (`make eval-data`); the expected files say what a correct run finds.
Messages name pages and counts only, never page text (rule 6).
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.db import Database
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import BUNDLE, Harness, expected, harness, recorded_book
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="the 900-page bundle runs with RUN_SLOW=1"),
]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def bundle_storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture(scope="module")
async def bundle_api_db(api_dsn: str) -> AsyncIterator[Database]:
    database = Database(api_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture(scope="module")
async def bundle(
    admin: psycopg.Connection,
    bundle_api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    bundle_storage: SupabaseStorage,
) -> AsyncIterator[Harness]:
    """records.pdf uploaded and processed once under the launch packs."""
    seed = Seed(admin)
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    store = FixtureStore(BUNDLE / "recordings")
    case = expected("case.json")
    async with harness(
        admin=admin,
        api_db=bundle_api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=bundle_storage,
        expert=expert,
        case_id=case_id,
        ocr_store=store,
        bedrock=recorded_book(store),
        names=case["case_names"],
        dates_of_birth=[dt.date.fromisoformat(case["dob"])],
    ) as h:
        await h.upload((BUNDLE / "records.pdf").read_bytes())
        finished = await h.drain(timeout=3600)
        assert set(finished.values()) == {"done"}, finished
        yield h


def numbered(h: Harness) -> dict[int, dict[str, Any]]:
    return {int(p["bundle_page_no"]): p for p in h.pages()}


@pytest.fixture(scope="module")
def pages(bundle: Harness) -> Iterator[dict[int, dict[str, Any]]]:
    yield numbered(bundle)


def test_every_page_is_numbered_once_in_order(pages: dict[int, dict[str, Any]]) -> None:
    assert sorted(pages) == list(range(1, 901))
    assert len({uuid.UUID(str(p["id"])) for p in pages.values()}) == 900


def test_born_digital_pages_use_their_text_layer_and_make_no_ocr_call(
    bundle: Harness, pages: dict[int, dict[str, Any]]
) -> None:
    wanted = {int(r["page"]): r["text_source"] for r in expected("text_source.json")}
    wrong = sorted(n for n, source in wanted.items() if pages[n]["text_source"] != source)
    assert wrong == [], f"pages with the wrong text source: {wrong}"
    embedded = sum(1 for s in wanted.values() if s == "embedded")
    assert bundle.ocr is not None
    assert len(bundle.ocr.sent) == len(wanted) - embedded, "OCR calls do not match the scanned pages"


def test_low_confidence_pages_are_queued_as_unreadable(pages: dict[int, dict[str, Any]]) -> None:
    wanted = set(expected("unreadable.json"))
    found = {n for n, p in pages.items() if p["unreadable"]}
    assert found == wanted, f"missing {sorted(wanted - found)}, extra {sorted(found - wanted)}"
