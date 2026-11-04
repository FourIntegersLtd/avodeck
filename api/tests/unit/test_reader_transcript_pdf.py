"""The PDF copy of the transcript: bookmarks, links to pictures, determinism, escaping (no database)."""

from __future__ import annotations

import io
import re
import uuid
from datetime import date
from typing import Any

import pytest
from PIL import Image
from pypdf import PdfReader

from app.features.reader.transcript import TranscriptEntry
from app.features.reader.transcript_layout import Coverage, Layout, Section
from app.features.reader.transcript_pdf import build_pdf


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (200, 100, 50)).save(buf, "JPEG")
    return buf.getvalue()


def _entry(page: int, lines: list[str] | None = None) -> TranscriptEntry:
    return TranscriptEntry(
        id=uuid.UUID(int=page),
        page_no=page,
        kind="consultation",
        kind_label="kind.consultation",
        folded=False,
        author_role=None,
        author_label="role.doctor",
        specialty=None,
        event_date=date(2020, 3, 4),
        date_basis=None,
        lines=lines or ["Line one"],
        results=[],
        result_labels=[],
    )


def _layout(lines: list[str] | None = None) -> Layout:
    return Layout(
        sections=[Section("a", "transcript.section.first_part", [_entry(1, lines), _entry(2)])],
        no_entry_pages=[3],
        unread_pages=[4],
        coverage=Coverage(total_pages=4, with_entries=2, no_entries=1, unreadable=1, set_aside=0),
    )


def _build(layout: Layout | None = None, pictures: Any = None, prepared: date | None = None) -> bytes:
    pics = pictures if pictures is not None else [(n, _jpeg()) for n in (1, 2, 3, 4)]
    return build_pdf(
        layout or _layout(),
        pics,
        date_order="DMY",
        language="en",
        received={1: date(2020, 3, 9)},
        prepared_on=prepared,
    )


def _titles(outline: Any) -> list[str]:
    out: list[str] = []
    for item in outline:
        out.extend(_titles(item) if isinstance(item, list) else [item.title])
    return out


def _texts(reader: PdfReader) -> list[str]:
    return [p.extract_text() or "" for p in reader.pages]


def test_bookmarks_and_page_count() -> None:
    reader = PdfReader(io.BytesIO(_build()))
    titles = _titles(reader.outline)
    assert "1. First part" in titles
    assert "Appendix A Pages with no entries" in titles  # layout changed by the transcript-document plan
    assert "Appendix B Pages the computer couldn't read" in titles
    assert "Appendix C Pictures of the pages" in titles
    assert len(reader.pages) >= 1 + 4


def test_every_link_lands_on_a_picture_page() -> None:
    reader = PdfReader(io.BytesIO(_build()))
    picture_pages = set(range(len(reader.pages) - 4, len(reader.pages)))  # the four pictures close the file
    heading_pages = set()  # the contents rows may also land on a heading listed in the PDF outline
    stack: list[Any] = [reader.outline]
    while stack:
        for item in stack.pop():
            if isinstance(item, list):
                stack.append(item)
            else:
                heading_pages.add(reader.get_destination_page_number(item))
    links = 0
    for page in reader.pages:
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Link":
                continue
            links += 1
            assert reader.get_page_number(annot["/Dest"][0].get_object()) in picture_pages | heading_pages
    assert links >= 4  # two entries plus the no-entry and unread pages, and the contents rows


def test_deterministic() -> None:
    assert _build() == _build()


def test_angle_brackets_and_ampersand_escaped() -> None:
    reader = PdfReader(io.BytesIO(_build(_layout(["Size <5mm & >3"]))))
    assert "<5mm & >3" in "".join(p.extract_text() or "" for p in reader.pages)


def test_missing_picture_shows_word_and_builds() -> None:
    pics = [(1, _jpeg()), (2, None), (3, b"not an image"), (4, _jpeg())]
    reader = PdfReader(io.BytesIO(_build(pictures=pics)))
    text = "".join(p.extract_text() or "" for p in reader.pages)
    assert text.count("Picture not available") == 2
    assert len(reader.pages) >= 1 + 4


def test_cover_holds_the_not_checked_words_and_no_page_number() -> None:
    first = _texts(PdfReader(io.BytesIO(_build())))[0]
    assert "This file has not been checked by the expert" in first
    assert "Records transcript" in first
    assert "4 pages in the bundle" in first
    assert "Page 1 of" not in first


def test_later_pages_say_page_x_of_n() -> None:
    reader = PdfReader(io.BytesIO(_build()))
    texts = _texts(reader)
    assert f"Page 2 of {len(reader.pages)}" in texts[1]
    assert f"Page {len(reader.pages)} of {len(reader.pages)}" in texts[-1]
    assert "Not checked by the expert" in texts[1]


def test_contents_gives_the_page_where_each_heading_is() -> None:
    reader = PdfReader(io.BytesIO(_build()))
    texts = _texts(reader)
    assert "Contents" in texts[1]
    contents = texts[1].splitlines()  # pypdf prints a row's dots and page number just before its words
    for heading, row in (
        ("1. First part", "First part"),
        ("Appendix A Pages with no entries", "Appendix A"),
        ("Appendix B Pages the computer couldn't read", "Appendix B"),
        ("Appendix C Pictures of the pages", "Appendix C"),
    ):
        at = next(i for i, ln in enumerate(contents) if row in ln)
        listed = int(re.search(r"(\d+)\s*$", contents[at - 1]).group(1))  # type: ignore[union-attr]
        probe = heading.replace("  ", " ")
        found = next(i for i in range(2, len(texts)) if probe in " ".join(texts[i].split()))
        assert listed == found + 1, heading


def test_entries_sit_under_year_and_month_headings_and_the_cover_names_the_date_prepared() -> None:
    reader = PdfReader(io.BytesIO(_build(prepared=date(2026, 11, 3))))
    texts = _texts(reader)
    assert "Prepared on 03/11/2026" in texts[0]
    body = " ".join(" ".join(texts).split())
    assert "2020" in body and "MARCH" in body


def test_text_outside_plain_ascii_comes_out_unchanged() -> None:
    reader = PdfReader(io.BytesIO(_build(_layout(["Łódź – 5 € ≤ 6"]))))
    assert "Łódź – 5 € ≤ 6" in "".join(_texts(reader))


def test_a_long_entry_continues_on_the_next_page() -> None:
    lines = [f"Line number {i} " + "word " * 30 for i in range(120)]
    reader = PdfReader(io.BytesIO(_build(_layout(lines))))
    text = "".join(_texts(reader))
    assert "Line number 0 " in text and "Line number 119 " in text


def test_prepared_on_is_optional() -> None:
    pdf = build_pdf(_layout(), [(n, _jpeg()) for n in (1, 2, 3, 4)], date_order="DMY", language="en", received={})
    assert "Prepared on" not in _texts(PdfReader(io.BytesIO(pdf)))[0]


def _with_around(entry: TranscriptEntry, before: list[str], after: list[str]) -> TranscriptEntry:
    return TranscriptEntry(**{**entry.__dict__, "before": before, "after": after})


def _complete_layout() -> Layout:
    """Six words printed in all (four in two entries, two on the page with no entries); nine pages in the bundle."""
    return Layout(
        sections=[Section("a", "transcript.section.first_part", [_entry(1), _entry(2)])],
        no_entry_pages=[3],
        unread_pages=[4],
        coverage=Coverage(
            total_pages=4, with_entries=2, no_entries=1, unreadable=1, set_aside=5, bundle_pages=9,
            words_read=6, words_printed=6,
        ),
        picture_words={3: ["Stray words"]},
    )  # fmt: skip


def _normal(reader: PdfReader) -> str:
    return " ".join(" ".join(_texts(reader)).split())


def test_words_outside_the_entries_print_under_a_label_before_and_after_the_entry() -> None:
    entry = _with_around(_entry(1), ["Top of page"], ["Foot of page"])
    layout = Layout([Section("a", "transcript.section.first_part", [entry])], [], [], Coverage(1, 1, 0, 0, 0))
    text = _normal(PdfReader(io.BytesIO(_build(layout, [(1, _jpeg())]))))
    assert text.count("Also on this page") == 2
    assert text.index("Top of page") < text.index("Line one") < text.index("Foot of page")


def test_a_page_with_no_entries_prints_its_words_under_its_number_with_the_new_sentence() -> None:
    text = _normal(PdfReader(io.BytesIO(_build(_complete_layout()))))
    assert (
        "The computer found no entries on these pages. The words it read are below; check them against the picture."
        in text
    )
    assert "These pages have no entries to set out" not in text
    assert "Stray words" in text


def test_the_cover_counts_the_whole_bundle_and_the_table_adds_up() -> None:
    reader = PdfReader(io.BytesIO(_build(_complete_layout())))
    assert "9 pages in the bundle" in _texts(reader)[0]
    body = _normal(reader)
    assert "Pages in the bundle 9" in body
    assert "Pages in this transcript 4" in body
    assert "(copies, replaced or held back) 5" in body  # 4 + 5 = 9
    assert "Every word the computer read on these pages is printed (6 words)" in body


def test_a_file_that_would_drop_a_word_is_never_built() -> None:
    layout = _complete_layout()
    short = Layout(
        layout.sections,
        layout.no_entry_pages,
        layout.unread_pages,
        Coverage(4, 2, 1, 1, 5, bundle_pages=9, words_read=7, words_printed=6),
        {3: ["Stray words"]},
    )
    with pytest.raises(ValueError):
        _build(short)


def test_the_pictures_heading_shares_its_page_with_the_first_picture() -> None:
    reader = PdfReader(io.BytesIO(_build()))
    texts = [" ".join(t.split()) for t in _texts(reader)]
    heading = next(i for i, t in enumerate(texts) if i > 1 and "Appendix C Pictures of the pages" in t)
    assert heading == len(reader.pages) - 4  # the page of picture 1


def test_a_picture_wider_than_it_is_tall_goes_on_a_landscape_page() -> None:
    wide = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(wide, "JPEG")
    pics = [(1, _jpeg()), (2, wide.getvalue()), (3, _jpeg()), (4, _jpeg())]
    reader = PdfReader(io.BytesIO(_build(pictures=pics)))
    sizes = [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages[-4:]]
    assert [w > h for w, h in sizes] == [False, True, False, False]


def test_no_outline_title_has_two_spaces_in_a_row() -> None:
    titles = _titles(PdfReader(io.BytesIO(_build())).outline)
    assert titles and all("  " not in t for t in titles)
