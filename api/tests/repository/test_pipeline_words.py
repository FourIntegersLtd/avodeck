"""Slice A increment 2: each page's words, from its text layer or from OCR (T-07a, architecture 7 step 2).

Born-digital pages take their words from the embedded text layer and are never sent to OCR
(`text_source = 'embedded'`, confidence 1). Scans, and the page with a typed header over a scanned
body, go to OCR (`text_source = 'ocr'`). Every word is kept in `ocr_words`, sealed with the case key,
exactly as read: nothing is corrected. A page read with low confidence is marked unreadable (the
screen 10 queue); handwriting is marked. Runs on the committed 18-page subset; messages name pages only.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.ocr import RecordedOcr
from app.core.ai.recordings import FixtureStore
from app.core.db import Database
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import (
    Harness,
    harness,
    recorded_book,
    rekeyed_store,
    subset_document,
    subset_entries,
    subset_page_bytes,
    text_pdf,
)
from tests.support.ai import ScriptedBedrock
from tests.support.files import local_storage
from tests.support.packs import BOTH, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

UNREADABLE_BELOW = 0.6  # mean word confidence under which a page goes to screen 10


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def recordings(tmp_path: Path) -> FixtureStore:
    return rekeyed_store(tmp_path / "recordings", subset_document(), subset_page_bytes())


async def _open(
    combo: Combo,
    store: FixtureStore,
    bedrock: Any,
    *,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> Any:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    return harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        ocr_store=store,
        bedrock=bedrock,
    )


@pytest.fixture
async def subset(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    """The 18-page subset uploaded as one file and processed under the launch packs."""
    opened = await _open(
        LAUNCH,
        recordings,
        recorded_book(recordings),
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    )
    async with opened as h:
        await h.upload(subset_document())
        finished = await h.drain(timeout=300)
        assert set(finished.values()) == {"done"}, finished
        yield h


def by_number(h: Harness) -> dict[int, dict[str, Any]]:
    """The case's pages keyed by their number in the full bundle (the subset keeps the bundle's order)."""
    numbers = [int(p["page"]) for p in subset_entries()]
    pages = h.pages()
    assert len(pages) == len(numbers)
    return dict(zip(numbers, pages, strict=True))


def words_of(h: Harness, page_id: uuid.UUID) -> list[dict[str, Any]]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    return cur.execute("select * from app.ocr_words where page_id = %s order by n", (page_id,)).fetchall()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def test_born_digital_pages_use_their_text_layer_and_scans_go_to_ocr(subset: Harness) -> None:
    pages = by_number(subset)
    cut = split_pages(subset_document())
    wanted: set[str] = set()
    for i, entry in enumerate(subset_entries()):
        n = int(entry["page"])
        assert pages[n]["text_source"] == entry["text_source"], f"page {n}"
        if entry["text_source"] == "ocr":
            wanted.add(hashlib.sha256(cut[i]).hexdigest())
    assert subset.ocr is not None
    assert set(subset.ocr.sent) == wanted, "OCR was sent the wrong pages"
    assert len(subset.ocr.sent) == len(wanted), "a page was sent to OCR twice"


async def test_the_typed_header_over_a_scanned_body_goes_to_ocr(subset: Harness) -> None:
    pages = by_number(subset)
    planted = [int(e["page"]) for e in subset_entries() if e["typed_header_scanned_body"]]
    assert planted, "the subset has no typed-header scan"
    for n in planted:
        assert pages[n]["text_source"] == "ocr", f"page {n}"


async def test_every_word_is_kept_sealed_and_exactly_as_read(subset: Harness, recordings: FixtureStore) -> None:
    from app.features.pipeline.paths import word_purpose

    pages = by_number(subset)
    cut = split_pages(subset_document())
    for i, entry in enumerate(subset_entries()):
        n = int(entry["page"])
        page = pages[n]
        if entry["text_source"] == "embedded":
            read = [(w.id, w.text, w.confidence) for w in embedded_words(cut[i]).words]
        else:
            recorded = recordings.lookup("ocr", RecordedOcr.request(cut[i], "layout"))
            read = [(w["id"], w["text"], w["confidence"]) for w in recorded["pages"][0]["words"]]
        stored = words_of(subset, page["id"])
        assert [r["word_id"] for r in stored] == [w[0] for w in read], f"page {n}"
        assert all(r["source"] == entry["text_source"] for r in stored), f"page {n}"
        for row, (word_id, text, confidence) in zip(stored, read, strict=True):
            sealed = bytes(row["text"])
            # Words long enough not to turn up in random bytes by chance; a bool, so no word is printed.
            unsealed = len(text) >= 6 and text.encode() in sealed
            assert not unsealed, f"page {n}: a word is stored unsealed"
            opened = await subset.decrypt(sealed, word_purpose(page["id"], word_id))
            assert digest(opened.decode()) == digest(text), f"page {n}: a word changed"
            assert abs(row["confidence"] - confidence) < 1e-4, f"page {n}"
            assert len(row["polygon"]) == 8, f"page {n}"


async def test_low_confidence_pages_go_to_the_unreadable_queue_and_handwriting_is_marked(subset: Harness) -> None:
    pages = by_number(subset)
    for entry in subset_entries():
        n = int(entry["page"])
        page = pages[n]
        assert page["unreadable"] is bool(entry["unreadable"]), f"page {n}"
        if entry["text_source"] == "embedded":
            assert page["ocr_confidence"] == 1.0 and page["handwriting"] is False, f"page {n}"
        elif entry["unreadable"]:
            assert page["ocr_confidence"] < UNREADABLE_BELOW, f"page {n}"
        else:
            assert page["ocr_confidence"] >= UNREADABLE_BELOW, f"page {n}"
    handwritten = {n for n, p in pages.items() if p["handwriting"]}
    assert {34, 463} <= handwritten, "handwritten pages are not marked"
    assert 1 not in handwritten and 93 not in handwritten


async def test_running_again_sends_nothing_more_to_ocr_and_keeps_the_words(subset: Harness) -> None:
    assert subset.ocr is not None
    sent = list(subset.ocr.sent)
    count = subset.admin.execute("select count(*) from app.ocr_words where case_id = %s", (subset.case_id,)).fetchone()
    job = await subset.enqueue("process_bundle")
    finished = await subset.drain(timeout=300)
    assert finished[job] == "done"
    assert subset.ocr.sent == sent
    again = subset.admin.execute("select count(*) from app.ocr_words where case_id = %s", (subset.case_id,)).fetchone()
    assert again == count


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
async def test_a_typed_file_makes_no_ocr_call_under_either_pack(
    combo: Combo,
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    from tests.repository.test_pipeline_support import empty_answer

    first_source = default_library().combine(*combo.codes).source_types()[0]["key"]
    opened = await _open(
        combo,
        FixtureStore(tmp_path / "nothing-recorded"),
        ScriptedBedrock([empty_answer(first_source)]),
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    )
    async with opened as h:
        await h.upload(text_pdf([["Synthetic typed page one", "second line"], ["Synthetic typed page two"]]))
        finished = await h.drain()
        assert set(finished.values()) == {"done"}
        assert h.ocr is not None and h.ocr.sent == []
        for page in h.pages():
            assert page["text_source"] == "embedded", f"page {page['bundle_page_no']}"
            assert len(words_of(h, page["id"])) >= 4, f"page {page['bundle_page_no']}"


async def test_low_confidence_handwriting_is_never_auto_corrected(subset: Harness, recordings: FixtureStore) -> None:
    """Brief: handwriting is never auto-corrected. Every word on a handwritten or unreadable page is
    stored exactly as OCR read it, with OCR's own confidence, and the page waits for a person."""
    from app.features.pipeline.paths import word_purpose

    pages = by_number(subset)
    cut = split_pages(subset_document())
    checked = 0
    for i, entry in enumerate(subset_entries()):
        n = int(entry["page"])
        page = pages[n]
        if entry["text_source"] == "embedded" or not (page["handwriting"] or page["unreadable"]):
            continue
        recorded = recordings.lookup("ocr", RecordedOcr.request(cut[i], "layout"))["pages"][0]["words"]
        stored = words_of(subset, page["id"])
        assert [r["word_id"] for r in stored] == [w["id"] for w in recorded], f"page {n}"
        for row, word in zip(stored, recorded, strict=True):
            assert row["source"] == "ocr", f"page {n}"
            opened = await subset.decrypt(bytes(row["text"]), word_purpose(page["id"], word["id"]))
            assert digest(opened.decode()) == digest(word["text"]), f"page {n}: a handwritten word was changed"
            assert abs(row["confidence"] - word["confidence"]) < 1e-4, f"page {n}"
        if page["unreadable"]:
            assert page["transcribed_by_expert_text"] is None, f"page {n}: text was filled in for a person"
        checked += 1
    assert checked >= 2, "the subset has no handwritten or unreadable page to check"
