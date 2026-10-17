"""The committed pipeline recordings: a small slice of the synthetic bundle that plays back offline.

`evals/generator/recordings.py` writes OCR (T-07a) and answer-book (T-07b, T-07c) recordings for the
whole synthetic bundle, and copies about twenty pages of it here (`tests/fixtures/ai/pipeline/`) so
pipeline tests run without the 120 MB bundle. Each page is one single-page PDF exactly as core
`split_pages` cuts it. Scanned pages have an OCR recording keyed by the page's bytes; born-digital
pages have none (their words come from the text layer). Every recorded answer is keyed by the page's
words, and every value it gives is found in the words it cites (rule 2), except the planted bad
answers: one page answers badly then well on the retry, one answers badly every time, and one cites
crossed-out words. Assertion messages name pages only, never their text (rule 6).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.ai import provenance
from app.core.ai.answers import AnswerBook, page_subject, reference_subject
from app.core.ai.bedrock import ModelRequest
from app.core.ai.ocr import OcrPage, RecordedOcr, word_id
from app.core.ai.recordings import FixtureStore, RecordingMissing
from app.core.pdf import embedded_words
from tests.support.ai import CELL_ENV, FIXTURES, cell

pytestmark = pytest.mark.anyio

ROOT = FIXTURES / "pipeline"
MAX_BYTES = 3_000_000
A4_INCHES = (8.27, 11.69)


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    path = ROOT / "manifest.json"
    assert path.is_file(), "pipeline recordings are not committed"
    data: dict[str, Any] = json.loads(path.read_text())
    return data


@pytest.fixture
def store() -> FixtureStore:
    return FixtureStore(ROOT)


def page_bytes(entry: dict[str, Any]) -> bytes:
    return (ROOT / str(entry["file"])).read_bytes()


async def page_words(entry: dict[str, Any], store: FixtureStore) -> OcrPage:
    if entry["text_source"] == "ocr":
        result = await RecordedOcr(cell(), store).analyse(page_bytes(entry))
        assert len(result.pages) == 1, f"page {entry['page']}"
        return result.pages[0]
    return embedded_words(page_bytes(entry))


def request(task_id: str, subject: str) -> ModelRequest:
    return ModelRequest(
        model_id=CELL_ENV["BEDROCK_SONNET_MODEL_ID"],
        system="",
        context={},
        text="",
        schema={},
        task_id=task_id,
        subject=subject,
    )


def cited(output: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(output["facts"]) + list(output["references_out"]) + list(output["availability_evidence"])
    return items + list(output["actions_requested_of_clinician"])


def test_the_subset_is_small_and_covers_each_kind_of_page(manifest: dict[str, Any]) -> None:
    size = sum(p.stat().st_size for p in ROOT.rglob("*") if p.is_file())
    assert size < MAX_BYTES
    pages = manifest["pages"]
    assert 15 <= len(pages) <= 25
    assert {p["text_source"] for p in pages} == {"embedded", "ocr"}
    assert any(p["typed_header_scanned_body"] for p in pages)
    assert any(p["unreadable"] for p in pages)
    planted = manifest["planted"]
    numbers = {p["page"] for p in pages}
    assert {planted["retry"], planted["bad_twice"], planted["struck"]} <= numbers


async def test_scanned_pages_play_back_their_ocr_and_born_digital_pages_have_none(
    manifest: dict[str, Any], store: FixtureStore
) -> None:
    ocr = RecordedOcr(cell(), store)
    for entry in manifest["pages"]:
        n = entry["page"]
        if entry["text_source"] == "embedded":
            with pytest.raises(RecordingMissing):
                await ocr.analyse(page_bytes(entry))
            assert embedded_words(page_bytes(entry)).words, f"page {n}"
            continue
        page = (await ocr.analyse(page_bytes(entry))).pages[0]
        assert page.number == 1 and (page.width, page.height) == pytest.approx(A4_INCHES, abs=0.01), f"page {n}"
        assert [w.id for w in page.words] == [word_id(1, i) for i in range(1, len(page.words) + 1)], f"page {n}"
        for w in page.words:
            assert len(w.polygon) == 8 and 0 < w.confidence <= 1, f"page {n}"
            assert all(-0.5 <= v <= page.width + 0.5 for v in w.polygon[0::2]), f"page {n}"
            assert all(-0.5 <= v <= page.height + 0.5 for v in w.polygon[1::2]), f"page {n}"
        if entry["unreadable"]:
            assert sum(w.confidence for w in page.words) / len(page.words) < 0.6, f"page {n}"


async def test_page_with_a_typed_header_and_a_scanned_body_has_both(
    manifest: dict[str, Any], store: FixtureStore
) -> None:
    entry = next(p for p in manifest["pages"] if p["typed_header_scanned_body"])
    assert entry["text_source"] == "ocr"
    header = embedded_words(page_bytes(entry)).words
    whole = (await page_words(entry, store)).words
    assert header and len(whole) > len(header)


async def test_every_recorded_answer_cites_words_that_hold_its_value(
    manifest: dict[str, Any], store: FixtureStore
) -> None:
    planted = manifest["planted"]
    for entry in manifest["pages"]:
        n = entry["page"]
        page = await page_words(entry, store)
        words = {w.id: w.text for w in page.words}
        subject = page_subject((w.id, w.text) for w in page.words)
        output = (await AnswerBook(cell(), store).invoke(request("T-07b", subject))).output
        assert set(output) >= {"source_type", "printed_dates", "facts", "references_out"}, f"page {n}"
        for item in cited(output):
            assert set(item["word_ids"]) <= words.keys(), f"page {n}"
        if n in (planted["retry"], planted["bad_twice"]):
            continue
        for fact in output["facts"]:
            assert provenance.supported(fact["value"], fact["word_ids"], words), f"page {n}"


async def test_planted_bad_answers_fail_the_provenance_check_as_planned(
    manifest: dict[str, Any], store: FixtureStore
) -> None:
    by_page = {p["page"]: p for p in manifest["pages"]}

    async def three_answers(n: int) -> list[bool]:
        page = await page_words(by_page[n], store)
        words = {w.id: w.text for w in page.words}
        book = AnswerBook(cell(), store)
        subject = page_subject((w.id, w.text) for w in page.words)
        results = []
        for _ in range(3):
            facts = (await book.invoke(request("T-07b", subject))).output["facts"]
            results.append(all(provenance.supported(f["value"], f["word_ids"], words) for f in facts))
        return results

    assert await three_answers(manifest["planted"]["retry"]) == [False, True, True]
    assert await three_answers(manifest["planted"]["bad_twice"]) == [False, False, False]


async def test_a_planted_answer_cites_crossed_out_words(manifest: dict[str, Any], store: FixtureStore) -> None:
    entry = next(p for p in manifest["pages"] if p["page"] == manifest["planted"]["struck"])
    page = await page_words(entry, store)
    struck = {w.id for w in page.words if w.struck}
    assert struck
    output = (
        await AnswerBook(cell(), store).invoke(request("T-07b", page_subject((w.id, w.text) for w in page.words)))
    ).output
    assert any(f["word_ids"] and set(f["word_ids"]) <= struck for f in output["facts"])


async def test_references_are_answered_found_or_missing(manifest: dict[str, Any], store: FixtureStore) -> None:
    by_page = {p["page"]: p for p in manifest["pages"]}
    refs = manifest["references"]
    assert any(r["matched_page"] is None for r in refs) and any(r["matched_page"] for r in refs)
    for ref in refs:
        page = await page_words(by_page[ref["page"]], store)
        subject = reference_subject(page_subject((w.id, w.text) for w in page.words), ref["word_ids"])
        output = (await AnswerBook(cell(), store).invoke(request("T-07c", subject))).output
        assert output["matched_page"] == ref["matched_page"], f"page {ref['page']}"
        assert 0 <= output["confidence"] <= 1


def test_manifest_paths_stay_inside_the_fixture_folder(manifest: dict[str, Any]) -> None:
    for entry in manifest["pages"]:
        path = (ROOT / entry["file"]).resolve()
        assert path.is_file() and Path(ROOT.resolve()) in path.parents
