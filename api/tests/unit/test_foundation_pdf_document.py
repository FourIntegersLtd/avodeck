"""`app.core.pdf_document.render_record()`: a small plain PDF of labelled values (reportlab).

For records Avodeck hands out as a file, such as the certificate of deletion. The caller passes the
words (from its own words file) and the values; the helper adds nothing of its own, so a document
carries only what its caller chose (no patient details on a certificate).
"""

from __future__ import annotations

import pypdfium2 as pdfium

from app.core.pdf_document import Section, render_record


def text_of(pdf: bytes) -> str:
    doc = pdfium.PdfDocument(pdf)
    try:
        return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    finally:
        doc.close()


def test_a_record_is_a_pdf_with_its_title_labels_and_values() -> None:
    pdf = render_record(
        "Zz record",
        [
            Section(None, [("Zz reference", "abc-123"), ("Zz when", "2026-10-17")]),
            Section("Zz counts", [("pages", "3"), ("documents", "1")]),
        ],
    )
    assert pdf.startswith(b"%PDF-")
    text = text_of(pdf)
    for word in ("Zz record", "Zz reference", "abc-123", "Zz when", "2026-10-17", "Zz counts", "pages", "3"):
        assert word in text


def test_a_long_record_runs_onto_more_pages_and_long_values_wrap() -> None:
    rows = [(f"row {i}", "f" * 64) for i in range(120)]
    pdf = render_record("Zz long", [Section(None, rows)])
    doc = pdfium.PdfDocument(pdf)
    try:
        assert len(doc) > 1
    finally:
        doc.close()
    assert "row 119" in text_of(pdf)


def test_the_same_record_gives_the_same_bytes() -> None:
    sections = [Section(None, [("a", "b")])]
    assert render_record("Zz", sections) == render_record("Zz", sections)
