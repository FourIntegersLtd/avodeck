"""Shared drawing helpers for synthetic record generation.

Every document is drawn with reportlab onto A4 pages. A Doc keeps a cursor,
breaks pages automatically and records the facts placed on each page, so the
expected answers are produced from the same data that drew the page.

All people, places and numbers are invented. Every page carries a small
"Synthetic test record" footer so a page can never be mistaken for a real one.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import date, datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab import rl_config

rl_config.invariant = 1  # no creation time or random document ID: the same input gives the same bytes

W, H = A4
_FONT_DIRS = [
    os.environ.get("AVODECK_FONT_DIR", ""),
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/truetype/dejavu",
    os.path.expanduser("~/Library/Fonts"),
    "/Library/Fonts",
]


def _font(file: str) -> str:
    """Find a font file in the Linux (CI) or macOS font folders; set AVODECK_FONT_DIR to override."""
    for d in _FONT_DIRS:
        if d and os.path.exists(os.path.join(d, file)):
            return os.path.join(d, file)
    raise FileNotFoundError(f"font {file} not found; install Liberation and DejaVu fonts or set AVODECK_FONT_DIR")



_FONTS = {
    "Sans": _font("LiberationSans-Regular.ttf"),
    "Sans-B": _font("LiberationSans-Bold.ttf"),
    "Sans-I": _font("LiberationSans-Italic.ttf"),
    "Serif": _font("LiberationSerif-Regular.ttf"),
    "Serif-B": _font("LiberationSerif-Bold.ttf"),
    "Serif-I": _font("LiberationSerif-Italic.ttf"),
    "Mono": _font("LiberationMono-Regular.ttf"),
    "Mono-B": _font("LiberationMono-Bold.ttf"),
    "Cond": _font("DejaVuSansCondensed.ttf"),
    "Cond-B": _font("DejaVuSansCondensed-Bold.ttf"),
}
for _name, _path in _FONTS.items():
    if _name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_name, _path))

SYNTHETIC_FOOTER = "Synthetic test record - invented patient - not real clinical data"


def emis_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def s1_date(d: date) -> str:
    return d.strftime("%d %b %Y")


def uk_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def long_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


@dataclass
class Fact:
    kind: str                 # consultation | result | prescription | referral | letter | examination | safety_net | missed_appt | task
    value: str                # the exact text the extractor should return
    quote: str                # verbatim words on the page that contain the value
    event_date: str | None = None   # ISO date of the event
    fields: dict = field(default_factory=dict)
    planted: str | None = None      # planted item id from evals/README.md, if any


@dataclass
class PageInfo:
    doc_id: str
    page_in_doc: int
    source_type: str          # gp_record | hospital_letter | discharge_summary | result | ooh | referral | lloyd_george | admin
    facts: list[Fact] = field(default_factory=list)
    printed_dates: list[str] = field(default_factory=list)
    availability: list[dict] = field(default_factory=list)   # {kind, date, quote}
    references_out: list[dict] = field(default_factory=list) # {described_document, date, quote}
    notes: list[str] = field(default_factory=list)


class Doc:
    """A multi-page document with a flowing cursor."""

    def __init__(self, doc_id: str, source_type: str, *, margin_l=18 * mm, margin_r=18 * mm,
                 margin_t=20 * mm, margin_b=20 * mm, header=None, footer=None):
        self.doc_id = doc_id
        self.source_type = source_type
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4)
        self.c.setTitle(doc_id)
        self.ml, self.mr, self.mt, self.mb = margin_l, margin_r, margin_t, margin_b
        self.header, self.footer = header, footer
        self.pages: list[PageInfo] = []
        self._new_page()

    # page handling -------------------------------------------------------
    def _new_page(self):
        self.pages.append(PageInfo(self.doc_id, len(self.pages) + 1, self.source_type))
        self.y = H - self.mt
        if self.header:
            self.y = self.header(self, len(self.pages))

    def _finish_page(self):
        if self.footer:
            self.footer(self, len(self.pages))
        self.c.setFont("Sans", 6.5)
        self.c.setFillGray(0.45)
        self.c.drawCentredString(W / 2, 8 * mm, SYNTHETIC_FOOTER)
        self.c.setFillGray(0)

    def page_break(self):
        self._finish_page()
        self.c.showPage()
        self._new_page()

    def need(self, h: float):
        if self.y - h < self.mb:
            self.page_break()

    @property
    def page(self) -> PageInfo:
        return self.pages[-1]

    # recording -----------------------------------------------------------
    def fact(self, kind, value, quote=None, event_date=None, planted=None, **fields):
        self.page.facts.append(Fact(kind, value, quote or value,
                                    event_date.isoformat() if isinstance(event_date, date) else event_date,
                                    fields, planted))

    def printed_date(self, d: date):
        iso = d.isoformat()
        if iso not in self.page.printed_dates:
            self.page.printed_dates.append(iso)

    # drawing -------------------------------------------------------------
    def text(self, x, s, font="Sans", size=9, *, y=None, color=None, advance=True, lead=None):
        c = self.c
        c.setFont(font, size)
        if color:
            c.setFillColorRGB(*color)
        c.drawString(x, self.y if y is None else y, s)
        if color:
            c.setFillGray(0)
        if advance and y is None:
            self.y -= lead or size * 1.3

    def right(self, x, s, font="Sans", size=9, y=None):
        self.c.setFont(font, size)
        self.c.drawRightString(x, self.y if y is None else y, s)

    def wrap_lines(self, s, width, font="Sans", size=9):
        words, lines, cur = s.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if pdfmetrics.stringWidth(t, font, size) <= width:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or [""]

    def para(self, x, s, width, font="Sans", size=9, lead=None, keep=False):
        lead = lead or size * 1.3
        lines = self.wrap_lines(s, width, font, size)
        if keep:
            self.need(lead * len(lines))
        for ln in lines:
            self.need(lead)
            self.text(x, ln, font, size, lead=lead)
        return len(lines)

    def labelled(self, xl, xv, label, value, width, font="Sans", size=9, label_font="Sans-B"):
        """Label in a left column, wrapped value in the right column (EMIS style)."""
        lead = size * 1.3
        lines = self.wrap_lines(value, width, font, size)
        self.need(lead * min(len(lines), 3))
        self.text(xl, label, label_font, size, advance=False)
        for ln in lines:
            self.need(lead)
            self.text(xv, ln, font, size, lead=lead)

    def hline(self, x1=None, x2=None, w=0.5, gray=0.6):
        self.c.setLineWidth(w)
        self.c.setStrokeGray(gray)
        self.c.line(x1 or self.ml, self.y + 3, x2 or (W - self.mr), self.y + 3)
        self.c.setStrokeGray(0)

    def box(self, x, y, w, h, fill=None, stroke=0.6, lw=0.6):
        c = self.c
        c.setLineWidth(lw)
        c.setStrokeGray(stroke)
        if fill is not None:
            c.setFillGray(fill)
            c.rect(x, y, w, h, stroke=1, fill=1)
            c.setFillGray(0)
        else:
            c.rect(x, y, w, h, stroke=1, fill=0)
        c.setStrokeGray(0)

    def save(self, out_dir: str) -> str:
        if getattr(self, "_saved", None):
            return self._saved
        self._finish_page()
        self.c.save()
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{self.doc_id}.pdf")
        with open(path, "wb") as f:
            f.write(self.buf.getvalue())
        self._saved = path
        return path


def stamp_received(doc: Doc, d: date, x=None, y=None, angle=-8, text="RECEIVED"):
    """A red rubber-stamp style 'received' mark (availability evidence)."""
    c = doc.c
    x = x or (W - 70 * mm)
    y = y or (H - 62 * mm)
    c.saveState()
    c.translate(x, y)
    c.rotate(angle)
    c.setStrokeColorRGB(0.75, 0.1, 0.12)
    c.setFillColorRGB(0.75, 0.1, 0.12)
    c.setLineWidth(1.4)
    c.roundRect(0, 0, 52 * mm, 15 * mm, 3, stroke=1, fill=0)
    c.setFont("Sans-B", 10)
    c.drawCentredString(26 * mm, 9.2 * mm, text)
    c.setFont("Sans-B", 11)
    c.drawCentredString(26 * mm, 3.2 * mm, d.strftime("%d %b %Y").upper())
    c.restoreState()
    doc.page.availability.append({"kind": "received", "date": d.isoformat(),
                                  "quote": f"{text} {d.strftime('%d %b %Y').upper()}"})


def nhs_number_valid(n: str) -> bool:
    digits = [int(ch) for ch in n if ch.isdigit()]
    if len(digits) != 10:
        return False
    total = sum(d * (10 - i) for i, d in enumerate(digits[:9]))
    check = 11 - (total % 11)
    check = 0 if check == 11 else check
    return check != 10 and check == digits[9]


def fmt_nhs(n: str) -> str:
    d = "".join(ch for ch in n if ch.isdigit())
    return f"{d[:3]} {d[3:6]} {d[6:]}"


def ts(d: date, hm: str) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(d.year, d.month, d.day, h, m)
