"""The PDF copy's internal links: each one lands on the page it names, not on page 1 (no database)."""

from __future__ import annotations

import io
from typing import Any

from pypdf import PdfReader

from app.features.reader.transcript_layout import Coverage, Layout, Section
from app.features.reader.transcript_pdf import build_pdf
from tests.unit.test_reader_transcript_pdf import _entry, _jpeg


def _layout() -> Layout:
    return Layout(
        sections=[
            Section("a", "transcript.section.first_part", [_entry(1)]),
            Section("b", "transcript.section.second_part", [_entry(2)]),
        ],
        no_entry_pages=[3],
        unread_pages=[],
        coverage=Coverage(total_pages=3, with_entries=2, no_entries=1, unreadable=0, set_aside=0),
    )


def _reader() -> PdfReader:
    pdf = build_pdf(
        _layout(), [(n, _jpeg()) for n in (1, 2, 3)], date_order="DMY", language="en", received={}, prepared_on=None
    )
    return PdfReader(io.BytesIO(pdf))


def _text(reader: PdfReader, index: int) -> str:
    return " ".join((reader.pages[index].extract_text() or "").split())


def _first_page_with(reader: PdfReader, probe: str) -> int:
    return next(i for i in range(2, len(reader.pages)) if probe in _text(reader, i))  # after cover and contents


def _links(reader: PdfReader, index: int) -> list[int]:
    """Where each link on a page goes, as a page index, in the order the file lists them."""
    found: list[int] = []
    for ref in reader.pages[index].get("/Annots") or []:
        annot: Any = ref.get_object()
        if annot.get("/Subtype") == "/Link":
            at = reader.get_page_number(annot["/Dest"][0].get_object())
            assert at is not None
            found.append(at)
    return found


def _outline_pages(reader: PdfReader) -> dict[str, int]:
    found: dict[str, int] = {}
    stack: list[Any] = [reader.outline]
    while stack:
        for item in stack.pop():
            if isinstance(item, list):
                stack.append(item)
            else:
                found[item.title] = int(reader.get_destination_page_number(item) or 0)
    return found


def test_each_outline_entry_is_on_its_own_page() -> None:
    reader = _reader()
    one = _first_page_with(reader, "1. First part")
    two = _first_page_with(reader, "2. Second part")
    assert 2 <= one < two
    pages = _outline_pages(reader)
    assert pages["1. First part"] == one
    assert pages["2. Second part"] == two
    assert pages["Appendix A Pages with no entries"] == _first_page_with(reader, "Appendix A Pages with no entries")
    assert pages["Appendix B Pictures of the pages"] == _first_page_with(reader, "Appendix B Pictures of the pages")


def test_the_contents_rows_go_to_the_pages_they_list() -> None:
    reader = _reader()
    expected = {
        _first_page_with(reader, "1. First part"),
        _first_page_with(reader, "2. Second part"),
        _first_page_with(reader, "Appendix A Pages with no entries"),
        _first_page_with(reader, "Appendix B Pictures of the pages"),
    }
    went = _links(reader, 1)
    assert went and set(went) == expected
    assert went == sorted(went)  # in the order of the document


def test_each_entry_page_link_goes_to_that_page_picture() -> None:
    reader = _reader()
    first_picture = len(reader.pages) - 3
    one = _first_page_with(reader, "1. First part")
    two = _first_page_with(reader, "2. Second part")
    assert _links(reader, one) == [first_picture]  # entry on page 1
    assert _links(reader, two) == [first_picture + 1]  # entry on page 2
    appendix = _first_page_with(reader, "Appendix A Pages with no entries")
    assert _links(reader, appendix) == [first_picture + 2]  # page 3, which has no entries
