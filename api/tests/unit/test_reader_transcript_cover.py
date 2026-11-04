"""The cover of the transcript, as a PDF and as a Word file: a proper title page that fills the page (no database)."""

from __future__ import annotations

import io
from datetime import date
from typing import Any

from docx import Document
from pypdf import PdfReader

from app.features.reader import transcript_docx
from app.features.reader.transcript_pdf import build_pdf
from tests.unit.test_reader_transcript_pdf import _jpeg
from tests.unit.test_reader_transcript_pdf import _layout as pdf_layout

LABEL = "Case 42"
PREPARED = date(2026, 11, 3)

WORDS = (
    "Records transcript",
    "The words printed on every page of the records, set out in date order",
    "Prepared by Avodeck",
    "Case: Case 42",
    "4 pages in the bundle",
    "Prepared on 03/11/2026",
    "Pages with entries",
    "Pages the computer couldn't read",
    "This file has not been checked by the expert",
    "Select a page number to see the picture of that page",
)


def _cover(label: str | None = LABEL) -> tuple[str, list[tuple[float, str]]]:
    named: dict[str, Any] = {"case_label": label}  # a keyword the builders take once the cover lands
    pdf = build_pdf(
        pdf_layout(),
        [(n, _jpeg()) for n in (1, 2, 3, 4)],
        date_order="DMY",
        language="en",
        received={},
        prepared_on=PREPARED,
        **named,
    )
    page = PdfReader(io.BytesIO(pdf)).pages[0]
    seen: list[tuple[float, str]] = []

    def visit(text: str, cm: Any, tm: Any, _font: Any, _size: Any) -> None:
        if text.strip():
            seen.append((float(tm[5]) + float(cm[5]), text))  # a flowable's text is moved by `cm`, the band's is not

    page.extract_text(visitor_text=visit)
    return " ".join(" ".join(t for _, t in seen).split()), seen


def test_the_pdf_cover_carries_the_title_page_words() -> None:
    text, _ = _cover()
    for expected in WORDS:
        assert expected in text, expected
    assert "Page 1 of" not in text


def test_the_pdf_cover_says_how_many_pages_have_entries_and_could_not_be_read() -> None:
    _, seen = _cover()
    rows = {t.strip() for _, t in seen}
    assert {"2", "1"} <= rows  # two pages with entries, one the computer couldn't read (the layout's coverage)


def test_the_pdf_cover_has_no_label_line_without_a_label() -> None:
    text, _ = _cover(None)
    assert "Case:" not in text


def test_the_pdf_cover_fills_the_page() -> None:
    _, seen = _cover()
    ys = sorted({round(y) for y, _ in seen}, reverse=True)
    assert ys[0] > 700  # the title is up in the top band
    assert ys[-1] < 120  # something stands at the foot, not just white space
    gaps = [a - b for a, b in zip(ys, ys[1:], strict=False)]
    assert max(gaps) < 170  # no gap longer than about 6 cm anywhere between


def _word_texts(label: str | None = LABEL) -> list[str]:
    named: dict[str, Any] = {"case_label": label}
    data = transcript_docx.build(
        pdf_layout(), date_order="DMY", language="en", received={}, prepared_on=PREPARED, **named
    )
    return [p.text for p in Document(io.BytesIO(data)).paragraphs]


def test_the_word_cover_carries_the_same_words_and_keeps_its_first_three_paragraphs() -> None:
    texts = _word_texts()
    assert texts[1].startswith("This file has not been checked")
    assert texts[2] == "4 pages in the bundle"
    for expected in (WORDS[1], WORDS[2], WORDS[3], WORDS[5]):
        assert any(expected in t for t in texts[:12]), expected
    assert "Pages with entries: 2 · Pages the computer couldn't read: 1" in texts[:12]


def test_the_word_cover_does_not_tell_the_reader_to_select_a_picture_the_file_does_not_have() -> None:
    texts = _word_texts()
    last = max(i for i, t in enumerate(texts[:12]) if WORDS[2] in t)
    assert WORDS[9] not in texts[last + 1]


def test_the_word_cover_has_no_label_line_without_a_label() -> None:
    assert not any(t.startswith("Case:") for t in _word_texts(None))
