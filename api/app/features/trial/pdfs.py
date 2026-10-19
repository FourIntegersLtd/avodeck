"""The documents slice I builds with reportlab: the joint statement to sign, the trial pack (large print:
16 pt body, nothing under 14 pt) and the trial page number list. Wording comes from the packs and
`words.py`; the expert's words are printed as written.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

BODY_PT = 16
SMALLEST_PT = 14


@dataclass
class Block:
    """A heading (level 1 or 2) or a body paragraph."""

    text: str
    level: int = 0


@dataclass
class Document:
    title: str
    blocks: list[Block | None] = field(default_factory=list)  # None: start a new page

    def heading(self, text: str, level: int = 1) -> None:
        self.blocks.append(Block(text, level))

    def para(self, text: str) -> None:
        self.blocks.append(Block(text))

    def paras(self, texts: Iterable[str]) -> None:
        for text in texts:
            self.para(text)

    def page_break(self) -> None:
        self.blocks.append(None)


def _styles(body: int) -> dict[int, ParagraphStyle]:
    size = max(body, SMALLEST_PT)
    return {
        0: ParagraphStyle("body", fontName="Helvetica", fontSize=size, leading=size * 1.45, spaceAfter=size * 0.6),
        1: ParagraphStyle(
            "h1", fontName="Helvetica-Bold", fontSize=size + 6, leading=(size + 6) * 1.3, spaceAfter=size
        ),
        2: ParagraphStyle(
            "h2", fontName="Helvetica-Bold", fontSize=size + 2, leading=(size + 2) * 1.3, spaceAfter=size * 0.6
        ),
    }


def _canvas(size: int) -> type[Canvas]:
    """A canvas whose page preamble opens at `size` pt: reportlab otherwise writes a 12 pt default font
    into every page, which a large-print check (nothing under 14 pt) would rightly flag."""

    class LargePrint(Canvas):  # type: ignore[misc]  # reportlab ships no types
        def _make_preamble(self) -> None:
            super()._make_preamble()
            preamble: str = self._preamble  # type: ignore[has-type]
            self._preamble = preamble.replace(" 12 Tf 14.4 TL", f" {size} Tf {size * 1.2:g} TL")

    return LargePrint


def render(document: Document, *, body: int = BODY_PT) -> tuple[bytes, int]:
    """The PDF and its page count."""
    styles = _styles(body)
    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=document.title,
        invariant=1,
    )
    story: list[object] = [Paragraph(escape(document.title), styles[1]), Spacer(1, 4 * mm)]
    for block in document.blocks:
        if block is None:
            story.append(PageBreak())
            continue
        for part in block.text.split("\n"):
            if part.strip():
                story.append(Paragraph(escape(part), styles[min(block.level, 2)]))
    doc.build(story, canvasmaker=_canvas(max(body, SMALLEST_PT)))
    return out.getvalue(), int(doc.page)
