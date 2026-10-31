"""Slice A increment 5: copies are set aside, never deleted (planted item 1, F13, Q5).

After extraction and dates (the brief's order), each new page is compared with the case's other
pages; a copy is set aside (`pages.duplicate_of_page_id` = the earliest page it copies) and keeps its
number, pictures and suggested facts: nothing is deleted, so undo restores it whole (F13). Each page
keeps a picture hash and a word hash for later comparisons. Runs on the committed 18-page subset
(page 236 is a re-scan of page 99) and on typed pages under both pack combinations; messages name
pages only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.pipeline import paths
from tests.repository.test_pipeline_support import (
    Harness,
    empty_answer,
    expected,
    facts_of,
    harness,
    recorded_book,
    rekeyed_store,
    subset_document,
    subset_numbers,
    subset_page_bytes,
    text_pdf,
)
from tests.support.ai import ScriptedBedrock
from tests.support.db import scope_for
from tests.support.files import local_storage
from tests.support.packs import BOTH, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LETTER = [
    "Practice letter about the review appointment on 03/04/2024",
    "Blood pressure 128/82 and weight 71 kg were recorded",
    "Please book a further review in six weeks with the team",
]
OTHER_VALUE = [LETTER[0], "Blood pressure 138/82 and weight 71 kg were recorded", LETTER[2]]
OTHER_DATE = [LETTER[0].replace("03/04/2024", "05/04/2024"), *LETTER[1:]]


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


def by_number(h: Harness) -> dict[int, dict[str, Any]]:
    """The case's pages keyed by their number in the full bundle (the subset keeps the bundle's order)."""
    numbers = dict(enumerate(subset_numbers(), start=1))
    return {numbers[p["bundle_page_no"]]: p for p in h.pages()}


def analysis(h: Harness, page_id: uuid.UUID) -> dict[str, Any]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    row = cur.execute("select * from app.page_analysis where page_id = %s", (page_id,)).fetchone()
    assert row is not None
    return row


@pytest.fixture
async def subset(
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    recordings = rekeyed_store(tmp_path / "recordings", subset_document(), subset_page_bytes())
    case = expected("case.json")
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        ocr_store=recordings,
        bedrock=recorded_book(recordings),
        names=list(case["case_names"]),
        dates_of_birth=[dt.date.fromisoformat(case["dob"])],
    ) as h:
        await h.upload(subset_document())
        finished = await h.drain(timeout=300)
        assert set(finished.values()) == {"done"}, finished
        yield h


def planted_in_subset() -> dict[int, int]:
    numbers = set(subset_numbers())
    return {
        int(d["page"]): int(d["copy_of_page"])
        for d in expected("duplicates.json")
        if int(d["page"]) in numbers and int(d["copy_of_page"]) in numbers
    }


async def test_the_rescanned_page_is_set_aside_as_a_copy_of_the_original(subset: Harness) -> None:
    pages = by_number(subset)
    planted = planted_in_subset()
    assert planted == {236: 99}
    ids = {p["id"]: n for n, p in pages.items()}
    found = {n: ids[p["duplicate_of_page_id"]] for n, p in pages.items() if p["duplicate_of_page_id"]}
    assert found == planted, f"set aside {sorted(found)}"


async def test_a_copy_keeps_its_number_and_pictures_and_is_not_deleted(subset: Harness) -> None:
    pages = by_number(subset)
    copy = pages[236]
    assert len(pages) == 18
    assert copy["bundle_page_no"] == subset_numbers().index(236) + 1
    assert copy["image_path"]
    for path in (paths.page_image_path(subset.case_id, copy["id"]), paths.thumbnail_path(subset.case_id, copy["id"])):
        assert await subset.storage.get(path), f"page 236: {path.rsplit('/', 1)[-1]} missing"


async def test_a_copy_keeps_what_was_found_on_it(subset: Harness) -> None:
    pages = by_number(subset)
    copy = pages[236]
    assert analysis(subset, copy["id"])["extracted_at"] is not None, "page 236 was not read"
    assert copy["bundle_page_no"] in {f["bundle_page_no"] for f in facts_of(subset)}, "page 236 lost its facts"


async def test_each_page_keeps_a_picture_hash_and_a_word_hash(subset: Harness) -> None:
    from app.features.pipeline import duplicates

    for n, page in by_number(subset).items():
        row = analysis(subset, page["id"])
        assert row["phash"] is not None, f"page {n}"
        assert row["minhash"] is not None and len(row["minhash"]) == duplicates.NUM_PERM, f"page {n}"
        assert row["duplicate_checked_at"] is not None, f"page {n}"
    copy = analysis(subset, by_number(subset)[236]["id"])
    assert copy["duplicate_status"] == "suggested"
    assert analysis(subset, by_number(subset)[99]["id"])["duplicate_status"] is None


async def test_running_again_changes_nothing(subset: Harness) -> None:
    before = {p["id"]: p["duplicate_of_page_id"] for p in subset.pages()}
    checked = {p["id"]: analysis(subset, p["id"])["duplicate_checked_at"] for p in subset.pages()}
    await subset.enqueue("process_pages")
    assert set((await subset.drain()).values()) == {"done"}
    assert {p["id"]: p["duplicate_of_page_id"] for p in subset.pages()} == before
    assert {p["id"]: analysis(subset, p["id"])["duplicate_checked_at"] for p in subset.pages()} == checked


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
async def test_only_a_page_with_the_same_words_values_and_dates_is_a_copy(
    combo: Combo,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    pack = default_library().combine(*combo.codes)
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    bedrock = ScriptedBedrock([empty_answer(pack.source_types()[0]["key"])])
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=bedrock,
    ) as h:
        await h.upload(text_pdf([LETTER, OTHER_VALUE, LETTER, OTHER_DATE, LETTER]))
        assert set((await h.drain()).values()) == {"done"}
        pages = {p["bundle_page_no"]: p for p in h.pages()}
        copies = {n: p["duplicate_of_page_id"] for n, p in pages.items() if p["duplicate_of_page_id"]}
        assert copies == {3: pages[1]["id"], 5: pages[1]["id"]}
        assert len(bedrock.requests) == 5, "each page is read once, copies included"


async def test_the_review_columns_change_only_with_the_page_action_and_hashes_only_in_a_job(
    admin: psycopg.Connection, seed: Seed, api_db: Database, worker_dsn: str
) -> None:
    expert, viewer, stranger = seed.person(), seed.person(), seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert)
    seed.case_member(case_id, viewer, "viewer", added_by=expert)
    doc = seed.document(case_id)
    page_id = admin.execute(  # type: ignore[index]
        "insert into app.pages (case_id, document_id, bundle_page_no) values (%s, %s, 1) returning id", (case_id, doc)
    ).fetchone()[0]
    admin.execute("insert into app.page_analysis (page_id, case_id) values (%s, %s)", (page_id, case_id))
    change = "update app.page_analysis set duplicate_status = 'kept', duplicate_version = 1 where page_id = %s"
    for person in (viewer, stranger):
        async with api_db.request(scope_for(person)) as conn:
            cur = await conn.execute(change, (page_id,))
            assert cur.rowcount == 0
    async with api_db.request(scope_for(expert)) as conn:
        cur = await conn.execute(change, (page_id,))
        assert cur.rowcount == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("update app.page_analysis set phash = 1 where page_id = %s", (page_id,))
    worker_db = Database(worker_dsn, max_size=1)
    await worker_db.open()
    try:
        async with worker_db.job(case_id) as conn:
            cur = await conn.execute("update app.page_analysis set phash = 1 where page_id = %s", (page_id,))
            assert cur.rowcount == 1
    finally:
        await worker_db.close()
    row = admin.execute(
        "select duplicate_status, duplicate_version, phash from app.page_analysis where page_id = %s", (page_id,)
    ).fetchone()
    assert row == ("kept", 1, 1)
