"""The PDF copy of the records transcript (ADR 0035): the layout as text, then a picture of every page at the back.

Each entry's page number links to that page's picture; sections and the pictures part are bookmarked. Pictures are
held in memory only and never written to disk (ADR 0021).
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageBreak, PageTemplate, Paragraph, Spacer

from app.features.reader.transcript_docx import entry_heading
from app.features.reader.transcript_layout import Layout
from app.features.reader.transcript_words import label_text, word

PICTURE_EDGE = 1400
PICTURE_QUALITY = 60

_GREEN = HexColor("#0F3D2C")
_LINK = "#1E6B4E"
_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=12, leading=16)
_H1 = ParagraphStyle(
    "h1",
    parent=_BODY,
    fontName="Helvetica-Bold",
    fontSize=18,
    leading=22,
    textColor=_GREEN,
    spaceBefore=12,
    spaceAfter=6,
)
_H2 = ParagraphStyle(
    "h2",
    parent=_BODY,
    fontName="Helvetica-Bold",
    fontSize=15,
    leading=19,
    textColor=_GREEN,
    spaceBefore=10,
    spaceAfter=4,
)
_H3 = ParagraphStyle(
    "h3",
    parent=_BODY,
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=17,
    textColor=_GREEN,
    spaceBefore=8,
    spaceAfter=3,
)


class _Heading(Paragraph):
    def __init__(self, text: str, style: ParagraphStyle, *, mark: tuple[str, str, int] | None = None) -> None:
        super().__init__(text, style)
        self.mark = mark


class _PicturePage(Flowable):
    """One picture, fitted to the frame, with a caption; its bookmark is where the page links land."""

    def __init__(self, number: int, jpeg: bytes | None, caption: str, missing: str) -> None:
        super().__init__()
        self.number, self.jpeg, self.caption, self.missing = number, jpeg, caption, missing
        self.size: tuple[int, int] | None = None
        if jpeg is not None:
            self.size = _size(jpeg)

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:  # noqa: N803
        self.aw, self.ah = availWidth, availHeight - 2
        return self.aw, self.ah

    def draw(self) -> None:
        c = self.canv
        c.bookmarkPage(f"page-{self.number}")
        c.setFont("Helvetica", 10)
        c.drawString(0, self.ah - 10, self.caption)
        top = self.ah - 18
        if self.jpeg is None or self.size is None:
            c.setFont("Helvetica", 12)
            c.drawString(0, top - 14, self.missing)
            return
        w, h = self.size
        scale = min(self.aw / w, top / h)
        c.drawImage(ImageReader(io.BytesIO(self.jpeg)), 0, top - h * scale, w * scale, h * scale)


def _size(jpeg: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(jpeg)) as im:
        return im.size


def _prepare(data: bytes | None) -> bytes | None:
    """Grey, no longer than PICTURE_EDGE on its longest side, compressed; None when it cannot be read."""
    if data is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            grey = im.convert("L")
            grey.thumbnail((PICTURE_EDGE, PICTURE_EDGE))
            out = io.BytesIO()
            grey.save(out, "JPEG", quality=PICTURE_QUALITY)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - an undecodable picture is shown as missing, never raised or logged
        return None


class _Doc(BaseDocTemplate):
    def afterFlowable(self, flowable: Any) -> None:  # noqa: N802
        mark = getattr(flowable, "mark", None)
        if mark:
            key, title, level = mark
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, level, 0)


def _links(pages: list[int], language: str) -> str:
    return ", ".join(
        f'<a href="#page-{n}" color="{_LINK}">{escape(word(language, "page", number=n))}</a>' for n in pages
    )


def build_pdf(
    layout: Layout,
    pictures: Iterable[tuple[int, bytes | None]],
    *,
    date_order: str,
    language: str,
    received: dict[int, date],
) -> bytes:
    """`pictures` are (page number, image bytes) in page order; a page with no picture shows a "not available" line."""
    story: list[Flowable] = [
        Paragraph(escape(word(language, "title")), _H1),
        Paragraph(escape(word(language, "not_checked")), _BODY),
    ]
    cov = layout.coverage
    story.append(Paragraph(escape(word(language, "coverage")), _H2))
    story.append(
        Paragraph(
            escape(
                word(
                    language,
                    "coverage_line",
                    total=cov.total_pages,
                    with_entries=cov.with_entries,
                    no_entries=cov.no_entries,
                    unreadable=cov.unreadable,
                    set_aside=cov.set_aside,
                )
            ),
            _BODY,
        )
    )
    for i, section in enumerate(layout.sections):
        label = label_text(section.label)
        story.append(_Heading(escape(label), _H1, mark=(f"section-{i}", label, 0)))
        for entry in section.entries:
            parts = entry_heading(entry, date_order=date_order, language=language, received=received)
            head = f'<a href="#page-{entry.page_no}" color="{_LINK}">{escape(parts[0])}</a>'
            if len(parts) > 1:
                head += escape(", " + ", ".join(parts[1:]))
            story.append(Paragraph(head, _H3))
            story.extend(Paragraph(escape(line) or "&nbsp;", _BODY) for line in entry.lines)
    if layout.no_entry_pages:
        title = word(language, "no_entry_pages")
        story.append(_Heading(escape(title), _H1, mark=("no-entry", title, 0)))
        story.append(Paragraph(escape(word(language, "no_entry_lead")), _BODY))
        story.append(Paragraph(_links(layout.no_entry_pages, language), _BODY))
    if layout.unread_pages:
        title = word(language, "unread_pages")
        story.append(_Heading(escape(title), _H1, mark=("unread", title, 0)))
        story.append(Paragraph(_links(layout.unread_pages, language), _BODY))
    title = word(language, "pictures")
    story.append(_Heading(escape(title), _H1, mark=("pictures", title, 0)))
    story.append(Spacer(1, 4))
    for number, data in pictures:
        story.append(PageBreak())
        story.append(
            _PicturePage(
                number, _prepare(data), word(language, "page", number=number), word(language, "picture_missing")
            )
        )

    out = io.BytesIO()
    doc = _Doc(
        out, pagesize=A4, invariant=1, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame])])
    doc.build(story)
    return out.getvalue()
