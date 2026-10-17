"""A small plain PDF of labelled values, for records Avodeck hands out as a file (reportlab).

`render_record(title, sections)` lays out a title, then each section's optional heading and its
label/value rows as a two-column table that runs onto more pages and wraps long values. The caller
passes every word (from its own words file) and every value; the helper adds nothing of its own, so a
document carries only what its caller chose (a certificate of deletion: no patient details). The
output is byte-for-byte the same for the same input (no creation date or random id).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=18, leading=23, spaceAfter=6 * mm)
_HEADING = ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=13, leading=17, spaceBefore=4 * mm,
                          spaceAfter=2 * mm)  # fmt: skip
_LABEL = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=11, leading=14)
_VALUE = ParagraphStyle("value", fontName="Helvetica", fontSize=11, leading=14, wordWrap="CJK")


@dataclass(frozen=True)
class Section:
    heading: str | None
    rows: list[tuple[str, str]] = field(default_factory=list)


def _para(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def render_record(title: str, sections: list[Section]) -> bytes:
    out = io.BytesIO()
    margin = 20 * mm
    doc = SimpleDocTemplate(
        out, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
        title=title, author="", subject="", creator="", producer="", invariant=1,
    )  # fmt: skip
    width = A4[0] - 2 * margin
    story: list[object] = [_para(title, _TITLE)]
    for section in sections:
        if section.heading:
            story.append(_para(section.heading, _HEADING))
        if section.rows:
            table = Table(
                [[_para(label, _LABEL), _para(value, _VALUE)] for label, value in section.rows],
                colWidths=[width * 0.35, width * 0.65],
                repeatRows=0,
            )
            table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))  # fmt: skip
            story.append(table)
        story.append(Spacer(1, 2 * mm))
    doc.build(story)
    return out.getvalue()
