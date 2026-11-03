"""The Word copy of the transcript: order of parts, received dates, folded entries in full (no database)."""

from __future__ import annotations

import io
import re
import uuid
from datetime import date

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml.ns import qn

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
    first = heads.index("1. First part")
    assert (
        first
        < heads.index("2. Second part")
        < heads.index("Appendix A  Pages with no entries")
        < heads.index("Appendix B  Pages the computer couldn't read")
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


_W_ANCHOR = qn("w:anchor")
_W_NAME = qn("w:name")


def _anchors(doc: DocumentType) -> list[str]:
    return [h.get(_W_ANCHOR) for h in doc.element.body.iter(qn("w:hyperlink")) if h.get(_W_ANCHOR)]


def _bookmarks(doc: DocumentType) -> list[str]:
    return [b.get(_W_NAME) for b in doc.element.body.iter(qn("w:bookmarkStart"))]


def test_cover_then_contents_plain_paragraph_not_a_heading() -> None:
    paras = _doc().paragraphs
    assert paras[2].text == "7 pages in the bundle"
    contents = next(p for p in paras if p.text == "Contents")
    assert contents.style is not None and not contents.style.name.startswith("Heading")


def test_prepared_on_is_shown_when_given() -> None:
    data = build(_layout(), date_order="DMY", language="en", received={}, prepared_on=date(2026, 11, 3))
    texts = [p.text for p in Document(io.BytesIO(data)).paragraphs]
    assert "Prepared on 03/11/2026" in texts


def test_every_contents_link_has_a_bookmark_in_the_body() -> None:
    doc = _doc()
    anchors = _anchors(doc)
    assert len(anchors) == 4  # two sections, two appendices
    assert set(anchors) <= set(_bookmarks(doc))


def test_contents_lists_numbered_sections_and_appendices() -> None:
    texts = [p.text for p in _doc().paragraphs]
    start = texts.index("Contents")
    listed = texts[start + 1 : start + 5]
    assert listed == [
        "1. First part",
        "2. Second part",
        "Appendix A  Pages with no entries",
        "Appendix B  Pages the computer couldn't read",
    ]


def test_footer_carries_page_x_of_y_fields_and_not_checked_words() -> None:
    footer = _doc().sections[0].footer._element.xml
    assert "PAGE" in footer and "NUMPAGES" in footer
    assert "Not checked by the expert" in footer


def test_first_page_has_no_header_or_footer() -> None:
    section = _doc().sections[0]
    assert section.different_first_page_header_footer is True
    assert section.first_page_footer.paragraphs[0].text == ""
    assert "Records transcript" in section.header.paragraphs[0].text


def test_coverage_table_holds_the_coverage_numbers() -> None:
    rows = [[c.text for c in r.cells] for r in _doc().tables[0].rows]
    assert rows == [
        ["Pages in the bundle", "7"],
        ["Pages with entries", "2"],
        ["Pages with no entries", "2"],
        ["Pages the computer couldn't read", "1"],
        ["Pages set aside (copies, replaced or held back)", "0"],
    ]


def test_summary_table_has_a_header_and_one_row_per_section() -> None:
    rows = [[c.text for c in r.cells] for r in _doc().tables[1].rows]
    assert rows[0] == ["No.", "Section", "Entries", "Pages", "From", "To"]
    assert len(rows) == 3
    assert rows[1] == ["1", "First part", "1", "1", "04/03/2020", "04/03/2020"]


def test_year_and_month_headings_and_entry_heading_level() -> None:
    doc = _doc()
    styles = {p.text: p.style.name for p in doc.paragraphs if p.style is not None}
    assert styles["2020"] == "Heading 2"
    assert styles["March"] == "Heading 3"
    assert styles["Page 2, 04/03/2020, Consultation, Doctor, General"] == "Heading 4"


def test_undated_entries_sit_under_no_date() -> None:
    entry = _entry(3)
    entry = TranscriptEntry(**{**entry.__dict__, "event_date": None})
    lay = Layout([Section("a", "transcript.section.first_part", [entry])], [], [], Coverage(3, 1, 0, 0, 0))
    doc = Document(io.BytesIO(build(lay, date_order="DMY", language="en", received={})))
    heads = [p.text for p in doc.paragraphs if p.style is not None and p.style.name.startswith("Heading")]
    assert "No date" in heads


def test_bookmark_names_use_only_letters_digits_and_underscore() -> None:
    doc = _doc()
    names = [*_bookmarks(doc), *_anchors(doc)]
    assert names
    assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", n) for n in names)
    assert {"sec_1", "app_no_entries"} <= set(names)


def test_undated_entries_are_one_level_below_their_no_date_heading() -> None:
    entry = TranscriptEntry(**{**_entry(3).__dict__, "event_date": None})
    lay = Layout([Section("a", "transcript.section.first_part", [entry])], [], [], Coverage(3, 1, 0, 0, 0))
    doc = Document(io.BytesIO(build(lay, date_order="DMY", language="en", received={})))
    heads = [(p.text, p.style.name) for p in doc.paragraphs if p.style is not None]
    heads = [(text, style) for text, style in heads if style.startswith("Heading")]
    start = heads.index(("No date", "Heading 2"))
    assert heads[start + 1][1] == "Heading 3"  # the entry, one level below its year heading
    assert "Heading 4" not in [style for _, style in heads]
