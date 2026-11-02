"""The Word copy of the transcript: order of parts, received dates, folded entries in full (no database)."""

from __future__ import annotations

import io
import uuid
from datetime import date

from docx import Document
from docx.document import Document as DocumentType

from app.features.reader.transcript import TranscriptEntry
from app.features.reader.transcript_docx import build
from app.features.reader.transcript_layout import Coverage, Layout, Section


def _entry(page: int, *, folded: bool = False, lines: list[str] | None = None) -> TranscriptEntry:
    return TranscriptEntry(
        id=uuid.uuid4(),
        page_no=page,
        kind="consultation",
        kind_label="kind.consultation",
        folded=folded,
        author_role="role.doctor",
        author_label="role.doctor",
        specialty="s",
        event_date=date(2020, 3, 4),
        date_basis=None,
        lines=lines or ["Line one", "Line two"],
        results=[],
        result_labels=[],
        specialty_label="specialty.general",
    )


def _layout() -> Layout:
    return Layout(
        sections=[
            Section("a", "transcript.section.first_part", [_entry(1)]),
            Section("b", "transcript.section.second_part", [_entry(2, folded=True, lines=["Folded text kept"])]),
        ],
        no_entry_pages=[5, 6],
        unread_pages=[7],
        coverage=Coverage(total_pages=7, with_entries=2, no_entries=2, unreadable=1, set_aside=0),
    )


def _doc() -> DocumentType:
    data = build(_layout(), date_order="DMY", language="en", received={1: date(2020, 3, 9)})
    return Document(io.BytesIO(data))


def test_parts_in_order_with_not_checked_first() -> None:
    doc = _doc()
    paras = doc.paragraphs
    assert paras[0].style is not None and paras[0].style.name == "Title"
    assert paras[1].text.startswith("This file has not been checked")
    heads = [p.text for p in paras if p.style is not None and p.style.name.startswith("Heading")]
    first = heads.index("First part")
    assert (
        first
        < heads.index("Second part")
        < heads.index("Pages with no entries")
        < heads.index("Pages the computer couldn't read")
    )
    assert heads.index("Every page accounted for") < first


def test_entry_heading_shows_date_kind_author_specialty_received() -> None:
    texts = [p.text for p in _doc().paragraphs]
    assert "Page 1, 04/03/2020, Consultation, Doctor, General, Received 09/03/2020" in texts
    assert "Page 2, 04/03/2020, Consultation, Doctor, General" in texts


def test_folded_entry_printed_in_full_and_pages_listed() -> None:
    texts = [p.text for p in _doc().paragraphs]
    assert "Folded text kept" in texts
    assert any("5" in t and "6" in t for t in texts)
    assert any(t.strip().endswith("7") for t in texts)
