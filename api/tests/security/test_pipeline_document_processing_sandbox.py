"""Audit 13, DP-09: every parser call the pipeline makes on a stranger's file runs in a killable child process.

A stand-in that sleeps past the timeout, or exits its own process, replaces one parser call for one marked document
or page. A document-level call that fails marks that document `damaged`; a page-level call that fails marks that page
unreadable exactly as an OCR failure does (no words, listed as a page we could not read). The next document or page
is processed and the job does not fail. Nothing printed or asserted on is patient text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pikepdf
import pytest

from app.core import sandbox
from app.core.config import SandboxSettings
from app.features.pipeline import parsing
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support import pipeline_parse_stand_ins as stand_in
from tests.support.file_lifecycle_documents import _save, _typed_page, text_pdf, upload_bytes

pytestmark = pytest.mark.anyio

h = lifecycle.h  # the same harness fixtures
storage = lifecycle.storage
run, state = lifecycle.run, lifecycle.state

MODES = ["hang", "exit"]


@pytest.fixture(autouse=True)
async def short_timeout() -> AsyncIterator[None]:
    await sandbox.start(SandboxSettings(pool_size=2, timeout_seconds=6.0))
    yield
    await sandbox.stop()


def marked_document() -> bytes:
    return text_pdf(2, "marked") + b"\n" + stand_in.FILE_MARK + b"\n"


def three_pages(middle: str) -> bytes:
    pdf = pikepdf.new()
    for words in ("keep one", middle, "keep three"):
        _typed_page(pdf, words)
    return _save(pdf)


def words_of(h: Harness, page_id: Any) -> int:
    row = h.admin.execute("select count(*) from app.ocr_words where page_id = %s", (page_id,)).fetchone()
    assert row is not None
    return int(row[0])


def page_rows(h: Harness) -> list[dict[str, Any]]:
    return h.pages()


@pytest.mark.parametrize("mode", MODES)
async def test_a_page_count_that_hangs_or_dies_marks_that_document_damaged_and_the_next_one_goes_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(parsing, "count_pages", getattr(stand_in, f"count_{mode}"))
    bad = await run(h, marked_document(), "marked.pdf")
    good = await run(h, text_pdf(2, "after"), "after.pdf")
    assert state(h, bad)["unreadable_reason"] == "damaged" and state(h, bad)["page_rows"] == 0
    assert state(h, good)["unreadable_reason"] is None and state(h, good)["page_rows"] == 2, "control"


@pytest.mark.parametrize("mode", MODES)
async def test_a_conversion_that_hangs_or_dies_marks_that_document_damaged_and_the_next_one_goes_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    import io

    from docx import Document

    monkeypatch.setattr(parsing, "unpack_file", getattr(stand_in, f"unpack_{mode}"))
    note = Document()
    note.add_paragraph("synthetic note")
    out = io.BytesIO()
    note.save(out)
    bad = await run(h, out.getvalue(), f"{stand_in.FILE_NAME}.docx")
    good = await run(h, text_pdf(2, "after"), "after.pdf")
    assert state(h, bad)["unreadable_reason"] == "damaged" and state(h, bad)["page_rows"] == 0
    assert state(h, good)["page_rows"] == 2, "control"


@pytest.mark.parametrize("step", ["cut", "pictures"])
@pytest.mark.parametrize("mode", MODES)
async def test_a_page_that_cannot_be_cut_or_pictured_is_unreadable_and_the_next_page_goes_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch, step: str, mode: str
) -> None:
    name = "cut_pages" if step == "cut" else "pictures"
    monkeypatch.setattr(parsing, name, getattr(stand_in, f"{step}_{mode}"))
    await run(h, three_pages(f"{stand_in.MARK} two"), "three.pdf")
    first, middle, last = page_rows(h)
    assert middle["unreadable"] is True and words_of(h, middle["id"]) == 0
    for page in (first, last):
        assert page["unreadable"] is False and words_of(h, page["id"]) > 0, "control: the other pages are read"
        assert page["image_path"] is not None
    assert middle["image_path"] is None, "the page has no stand-in picture, so a later run cuts it again"


@pytest.mark.parametrize("mode", MODES)
async def test_a_text_layer_check_that_hangs_or_dies_sends_that_page_to_ocr(
    h: Harness, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(parsing, "text_layer", getattr(stand_in, f"layer_{mode}"))
    # DP-09 review P4: no usable text layer, so the page goes to OCR (none is wired here: the job pauses for it)
    await upload_bytes(h, three_pages(f"{stand_in.MARK} two"), filename="three.pdf")
    await h.drain()
    first, middle, last = page_rows(h)
    assert middle["unreadable"] is False and middle["text_source"] is None and words_of(h, middle["id"]) == 0
    # the job pauses at the page that needs OCR, so a page after it may not have been reached yet
    assert first["unreadable"] is False and last["unreadable"] is False
    assert words_of(h, first["id"]) + words_of(h, last["id"]) > 0, "control: the other pages are read"
