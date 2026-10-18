"""The PDF of a built report (reportlab) and its page images for the preview (pypdfium2).

The PDF follows the assembled sections in the same order as the Word file. It is a reading copy for the
screen 16 preview and for serving; a LibreOffice rendering of the Word file itself is a later step (brief).
"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

import pypdfium2
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.features.report.assemble import Assembly
from app.features.report.words import section_label


def render_pdf(assembly: Assembly, headings: dict[str, str]) -> tuple[bytes, int]:
    """The PDF and its page count. `headings` gives the template's heading words for mapped sections."""
    styles = getSampleStyleSheet()
    story: list[object] = []
    for block in assembly.blocks:
        story.append(Paragraph(escape(headings.get(block.key, section_label(block.key))), styles["Heading2"]))
        for para in block.paragraphs:
            story.append(Paragraph(escape(para.text), styles["BodyText"]))
        story.append(Spacer(1, 8))
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4).build(story)
    data = out.getvalue()
    return data, page_count(data)


def page_count(pdf: bytes) -> int:
    document = pypdfium2.PdfDocument(pdf)
    try:
        return len(document)
    finally:
        document.close()


def page_png(pdf: bytes, page_no: int, *, scale: float = 1.5) -> bytes | None:
    """One page as a PNG, numbered from 1; None if there is no such page."""
    document = pypdfium2.PdfDocument(pdf)
    try:
        if page_no < 1 or page_no > len(document):
            return None
        image = document[page_no - 1].render(scale=scale).to_pil()
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
    finally:
        document.close()
