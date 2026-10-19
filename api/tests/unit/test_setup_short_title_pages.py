"""Question short titles and PDF page counts (screens 04, 06; slice E's tabs show the title).

A short title is a label of at most 40 characters: the model's when it is a clean label, else the first
clause of the question's own words. A page count comes from the PDF's page tree only (no OCR) and is None
when the file is not a readable PDF.
"""

from __future__ import annotations

import io

import pytest
from reportlab.pdfgen import canvas

from app.features.setup.letter import short_title
from app.features.setup.pdf import page_count

PDF3 = io.BytesIO()
_c = canvas.Canvas(PDF3)
for _ in range(3):
    _c.drawString(72, 700, "page")
    _c.showPage()
_c.save()


def test_a_clean_model_label_is_used() -> None:
    text = "Was the care below the standard of a reasonable practitioner?"
    assert short_title(text, "Breach of duty") == "Breach of duty"


@pytest.mark.parametrize("label", [None, "", "   ", "x" * 41, "two\nlines"])
def test_an_unusable_label_falls_back_to_the_first_clause(label: str | None) -> None:
    text = "Please comment on the standard of care given on 14 May, and whether it fell below that expected."
    got = short_title(text, label)
    assert got == "Please comment on the standard of care"
    assert 0 < len(got) <= 40


def test_the_first_clause_is_cut_at_a_word_and_never_empty() -> None:
    long = "Having regard to everything in the records provided you are asked to say what happened"
    got = short_title(long, None)
    assert len(got) <= 40 and long.startswith(got) and not got.endswith(" ")
    assert short_title("?", None) == "?"


def test_a_pdf_reports_its_pages_from_the_page_tree() -> None:
    assert page_count(PDF3.getvalue()) == 3


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04 a word file", b"%PDF-1.7 broken"])
def test_anything_else_has_no_count(data: bytes) -> None:
    assert page_count(data) is None
