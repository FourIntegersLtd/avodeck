"""The PDF copy of the transcript: bookmarks, links to pictures, determinism, escaping (no database)."""

from __future__ import annotations

import io
import uuid
from datetime import date
from typing import Any

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


def _build(layout: Layout | None = None, pictures: Any = None) -> bytes:
    pics = pictures if pictures is not None else [(n, _jpeg()) for n in (1, 2, 3, 4)]
    return build_pdf(layout or _layout(), pics, date_order="DMY", language="en", received={1: date(2020, 3, 9)})


def _titles(outline: Any) -> list[str]:
    out: list[str] = []
    for item in outline:
        out.extend(_titles(item) if isinstance(item, list) else [item.title])
    return out


def test_bookmarks_and_page_count() -> None:
    reader = PdfReader(io.BytesIO(_build()))
    titles = _titles(reader.outline)
    assert "First part" in titles
    assert "Pictures of the pages" in titles
    assert "Pages with no entries" in titles
    assert len(reader.pages) >= 1 + 4


def test_every_link_lands_on_a_picture_page() -> None:
    reader = PdfReader(io.BytesIO(_build()))
    picture_pages = set(range(len(reader.pages) - 4, len(reader.pages)))  # the four pictures close the file
    links = 0
    for page in reader.pages:
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Link":
                continue
            links += 1
            assert reader.get_page_number(annot["/Dest"][0].get_object()) in picture_pages
    assert links >= 4  # two entries plus the no-entry and unread pages


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
