"""The PDF copy of the records transcript (ADR 0035): a cover, contents, an "about" page, numbered sections by year
and month, appendices and a picture of every page at the back.

Every entry's page number links to that page's picture; the contents rows link to their headings; sections, years and
appendices are bookmarked. Pictures are held in memory only and never written to disk (ADR 0021). The words are the
few in `transcript_words`; labels come from the pack (rule 11). Nothing is logged (rule 6).
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from app.features.reader.transcript import TranscriptEntry
from app.features.reader.transcript_document import (
    SummaryRow,
    appendix_letters,
    by_year,
    ensure_complete,
    numbered,
    summary,
)
from app.features.reader.transcript_docx import entry_heading
from app.features.reader.transcript_layout import Layout
from app.features.reader.transcript_words import month_name, show_date, word

PICTURE_EDGE = 1400
PICTURE_QUALITY = 60

_FONTS = Path(__file__).parent / "fonts"  # open fonts (SIL OFL), embedded in every file
_SANS, _SANS_BOLD, _MONO, _SERIF, _SERIF_BOLD = "Atkinson", "Atkinson-Bold", "PlexMono", "PTSerif", "PTSerif-Bold"
for _name, _file in (
    (_SANS, "AtkinsonHyperlegible-Regular.ttf"),
    (_SANS_BOLD, "AtkinsonHyperlegible-Bold.ttf"),
    (_MONO, "IBMPlexMono-Regular.ttf"),
    (_SERIF, "PT_Serif-Web-Regular.ttf"),
    (_SERIF_BOLD, "PT_Serif-Web-Bold.ttf"),
):
    pdfmetrics.registerFont(TTFont(_name, str(_FONTS / _file)))
pdfmetrics.registerFontFamily(_SANS, normal=_SANS, bold=_SANS_BOLD, italic=_SANS, boldItalic=_SANS_BOLD)
pdfmetrics.registerFontFamily(_SERIF, normal=_SERIF, bold=_SERIF_BOLD, italic=_SERIF, boldItalic=_SERIF_BOLD)
pdfmetrics.registerFontFamily(_MONO, normal=_MONO, bold=_MONO, italic=_MONO, boldItalic=_MONO)

_FOREST = HexColor("#0F3D2C")
_LINK = "#1E6B4E"
_INK = HexColor("#16201B")
_SLATE = HexColor("#4F5953")
_LINE = HexColor("#DDE3DF")
_MINT = HexColor("#E6F2EC")
_AMBER_FILL = HexColor("#FDF1E3")
_AMBER_EDGE = HexColor("#E8C9A6")
_AMBER_TEXT = HexColor("#8A3F06")

_MARGIN_X = 20 * mm
_MARGIN_Y = 22 * mm
_GUTTER = 30 * mm
_BAND = 84 * mm
_COVER_FOOT = 58 * mm  # the cover's frame stops here; the "how to use" line and the mark stand below

_BODY = ParagraphStyle("body", fontName=_SERIF, fontSize=10.5, leading=14.5, textColor=_INK)
_LEAD = ParagraphStyle("lead", parent=_BODY, fontName=_SANS, fontSize=11, leading=16, spaceAfter=6)
_H1 = ParagraphStyle("h1", fontName=_SANS_BOLD, fontSize=20, leading=26, textColor=_FOREST, spaceBefore=0, spaceAfter=6)
_H2 = ParagraphStyle(
    "h2", fontName=_SANS_BOLD, fontSize=14, leading=18, textColor=_FOREST, spaceBefore=14, spaceAfter=4
)
_H3 = ParagraphStyle("h3", fontName=_SANS_BOLD, fontSize=11, leading=14, textColor=_SLATE, spaceBefore=8, spaceAfter=2)
_META = ParagraphStyle("meta", fontName=_SANS, fontSize=9, leading=12, textColor=_SLATE, spaceAfter=2)
_ALSO = ParagraphStyle("also", parent=_BODY, fontSize=9, leading=12.5, textColor=_SLATE)  # words in no entry
_MONO_SMALL = ParagraphStyle("mono", fontName=_MONO, fontSize=9, leading=12, textColor=_SLATE)
_MONO_LINK = ParagraphStyle("monolink", parent=_MONO_SMALL, textColor=HexColor(_LINK))
_CELL = ParagraphStyle("cell", fontName=_SANS, fontSize=10, leading=13, textColor=_INK)
_CELL_HEAD = ParagraphStyle("cellhead", parent=_CELL, fontName=_SANS_BOLD, textColor=_FOREST)
_CELL_NUM = ParagraphStyle("cellnum", parent=_CELL, fontName=_MONO, alignment=TA_RIGHT)
_CELL_PAGE = ParagraphStyle("cellpage", parent=_MONO_LINK, alignment=TA_CENTER)
_PANEL = ParagraphStyle("panel", fontName=_SANS, fontSize=12, leading=17, textColor=_AMBER_TEXT)
_COVER_FACT = ParagraphStyle("coverfact", fontName=_SANS, fontSize=14, leading=20, textColor=_INK, spaceBefore=10)
_COVER_CASE = ParagraphStyle(
    "covercase", fontName=_SANS_BOLD, fontSize=22, leading=28, textColor=_FOREST, spaceBefore=4, spaceAfter=6
)
_TILE_NUM = ParagraphStyle("tilenum", fontName=_SANS_BOLD, fontSize=34, leading=40, textColor=_FOREST)
_TILE_LABEL = ParagraphStyle("tilelabel", fontName=_SANS, fontSize=12, leading=16, textColor=_SLATE)
_FOOT_USE = ParagraphStyle("footuse", fontName=_SANS, fontSize=11, leading=16, textColor=_SLATE)
_TOC0 = ParagraphStyle(
    "toc0", fontName=_SANS_BOLD, fontSize=11, leading=15, textColor=_FOREST, spaceBefore=5, rightIndent=22 * mm
)
_TOC1 = ParagraphStyle(
    "toc1", fontName=_SANS, fontSize=9.5, leading=13, textColor=_INK, leftIndent=12, rightIndent=22 * mm
)


class _Heading(Paragraph):  # type: ignore[misc]
    """A heading that is also a bookmark and, when it has a `level`, an outline and contents entry.

    `running` is the text the running header shows from here on; `rule` draws a thin mint line above."""

    def __init__(
        self,
        text: str,
        style: ParagraphStyle,
        *,
        key: str | None = None,
        title: str = "",
        level: int = 0,
        toc: bool = True,
        running: str | None = None,
        rule: bool = False,
    ) -> None:
        super().__init__(text, style)
        self.key, self.title, self.level, self.toc, self.running, self.rule = key, title, level, toc, running, rule

    def draw(self) -> None:
        super().draw()
        if self.rule:
            c = self.canv
            c.setStrokeColor(_MINT)
            c.setLineWidth(0.75)
            y = self.height + self.style.spaceBefore / 2
            c.line(0, y, self.width, y)


class _PicturePage(Flowable):  # type: ignore[misc]
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
        c.setFont(_SANS, 10)
        c.drawString(0, self.ah - 10, self.caption)
        top = self.ah - 18
        if self.jpeg is None or self.size is None:
            c.setFont(_SANS, 12)
            c.drawString(0, top - 14, self.missing)
            return
        w, h = self.size
        scale = min(self.aw / w, top / h)
        c.drawImage(ImageReader(io.BytesIO(self.jpeg)), 0, top - h * scale, w * scale, h * scale)


def _size(jpeg: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(jpeg)) as im:
        return im.size


def prepare_picture(data: bytes | None) -> bytes | None:
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


class _Doc(BaseDocTemplate):  # type: ignore[misc]
    section_text = ""  # what the running header shows; set as each heading is placed
    total_pages = 0  # the page count of the pass before; the footer prints it

    def afterFlowable(self, flowable: Any) -> None:  # noqa: N802
        if not isinstance(flowable, _Heading):
            return
        if flowable.running is not None:
            self.section_text = flowable.running
        if flowable.key:
            self.canv.bookmarkPage(flowable.key)
            self.canv.addOutlineEntry(flowable.title, flowable.key, flowable.level, 0)
            if flowable.toc:
                self.notify("TOCEntry", (flowable.level, escape(flowable.title), self.page, flowable.key))


class _PageTotal(Flowable):  # type: ignore[misc]
    """Takes up no room; tells `multiBuild` to go round again until the page count it measured is the count the
    footers printed ("Page n of N"). The footer is drawn as each page ends, so the pages are really added as they
    are made and every bookmark and link lands on its own page (a canvas that held the pages back put them all
    on page 1)."""

    def __init__(self, doc: _Doc) -> None:
        super().__init__()
        self.doc, self.settled = doc, False

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:  # noqa: N803
        return 0, 0

    def draw(self) -> None:
        return None

    def isIndexing(self) -> bool:  # noqa: N802
        return True

    def isSatisfied(self) -> bool:  # noqa: N802
        return self.settled

    def beforeBuild(self) -> None:  # noqa: N802
        return None

    def notify(self, kind: str, stuff: Any) -> None:
        return None

    def afterBuild(self) -> None:  # noqa: N802
        self.settled = self.doc.total_pages == self.doc.page
        self.doc.total_pages = self.doc.page


def _links(pages: list[int]) -> Table:
    """Linked page numbers, ten to a row."""
    cells = [Paragraph(f'<a href="#page-{n}" color="{_LINK}">{n}</a>', _CELL_PAGE) for n in pages]
    rows = [cells[i : i + 10] for i in range(0, len(cells), 10)]
    rows[-1] += [""] * (10 - len(rows[-1]))
    return Table(rows, colWidths=[(A4[0] - 2 * _MARGIN_X) / 10] * 10, hAlign="LEFT")


def _entry_table(
    entry: TranscriptEntry, *, width: float, date_order: str, language: str, received: dict[int, date]
) -> Table:
    """One entry: page number and date in the left column, then the meta line and one row per line of the record.
    One row per line lets a long entry carry on to the next page with nothing clipped."""
    parts = entry_heading(entry, date_order=date_order, language=language, received=received)
    link = f'<a href="#page-{entry.page_no}" color="{_LINK}">{escape(parts[0])}</a>'
    gutter: list[Flowable] = [Paragraph(link, _MONO_LINK), Paragraph(escape(parts[1]), _MONO_SMALL)]
    rows: list[list[Any]] = [[gutter, Paragraph(escape(" · ".join(parts[2:])), _META)]]
    label = escape(word(language, "also_on_page"))

    def around(lines: list[str]) -> list[list[Any]]:
        """The words of the page that lie in no entry: a small grey label, then the lines in a smaller grey face."""
        if not lines:
            return []
        return [["", Paragraph(label, _META)], *(["", Paragraph(escape(line) or "&nbsp;", _ALSO)] for line in lines)]

    rows.extend(around(entry.before))
    rows.extend(["", Paragraph(escape(line) or "&nbsp;", _BODY)] for line in entry.lines)
    rows.extend(around(entry.after))
    last = len(rows) - 1
    table = Table(rows, colWidths=[_GUTTER, width - _GUTTER], hAlign="LEFT", splitByRow=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, last), (-1, last), 6),
                ("LINEBELOW", (0, last), (-1, last), 0.5, _LINE),
            ]
        )
    )
    return table


def _summary_table(rows: list[SummaryRow], language: str, width: float) -> Table:
    head = [
        Paragraph(escape(word(language, k)), _CELL_HEAD)
        for k in ("col_number", "col_section", "col_entries", "col_pages", "col_from", "col_to")
    ]
    body = [
        [
            Paragraph(str(r.number), _CELL_NUM),
            Paragraph(escape(r.label), _CELL),
            Paragraph(str(r.entries), _CELL_NUM),
            Paragraph(str(r.pages), _CELL_NUM),
            Paragraph(escape(r.date_from), _CELL),
            Paragraph(escape(r.date_to), _CELL),
        ]
        for r in rows
    ]
    fixed = [12 * mm, 0.0, 20 * mm, 18 * mm, 24 * mm, 24 * mm]
    fixed[1] = width - sum(fixed)
    table = Table([head, *body], colWidths=fixed, hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _MINT),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, _LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _coverage_table(layout: Layout, language: str, width: float) -> Table:
    cov = layout.coverage
    rows = [
        (word(language, "cov_total"), cov.bundle_pages),
        (word(language, "cov_set_aside"), cov.set_aside),
        (word(language, "cov_in_transcript"), cov.total_pages),
        (word(language, "cov_with"), cov.with_entries),
        (word(language, "cov_none"), cov.no_entries),
        (word(language, "cov_unread"), cov.unreadable),
    ]
    cells = [[Paragraph(escape(label), _CELL), Paragraph(str(n), _CELL_NUM)] for label, n in rows]
    table = Table(cells, colWidths=[width - 30 * mm, 30 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, _LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _tiles(layout: Layout, language: str, width: float) -> Table:
    """Two big numbers: the pages with entries and the pages the computer could not read."""
    cov = layout.coverage
    cells = [
        [Paragraph(str(n), _TILE_NUM), Paragraph(escape(word(language, key)), _TILE_LABEL)]
        for key, n in (("cov_with", cov.with_entries), ("cov_unread", cov.unreadable))
    ]
    gap = 6 * mm
    table = Table([[cells[0], "", cells[1]]], colWidths=[(width - gap) / 2, gap, (width - gap) / 2], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), _MINT),
                ("BACKGROUND", (2, 0), (2, 0), _MINT),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
                ("LEFTPADDING", (1, 0), (1, 0), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ]
        )
    )
    return table


def _cover_story(
    layout: Layout, language: str, width: float, prepared_on: date | None, date_order: str, case_label: str | None
) -> list[Flowable]:
    """Below the band: the case, the page count and date, the two counts, and the notice that nothing is checked."""
    panel = Table([[Paragraph(escape(word(language, "not_checked")), _PANEL)]], colWidths=[width])
    panel.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _AMBER_FILL),
                ("BOX", (0, 0), (-1, -1), 0.75, _AMBER_EDGE),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story: list[Flowable] = []
    if case_label:
        story.append(Paragraph(escape(word(language, "case_line", label=case_label)), _COVER_CASE))
    story.append(Paragraph(escape(word(language, "bundle_pages", count=layout.coverage.bundle_pages)), _COVER_FACT))
    if prepared_on is not None:
        shown = show_date(prepared_on, date_order, language)
        story.append(Paragraph(escape(word(language, "prepared", date=shown)), _COVER_FACT))
    story += [Spacer(1, 14 * mm), _tiles(layout, language, width), Spacer(1, 14 * mm), panel]
    return story


def _page_words(layout: Layout, pages: list[int], language: str) -> list[Flowable]:
    """Under each of these pages' numbers (linked to its picture), the words the computer read there."""
    story: list[Flowable] = []
    for number in pages:
        lines = layout.picture_words.get(number, [])
        if lines:
            link = f'<a href="#page-{number}" color="{_LINK}">{escape(word(language, "page", number=number))}</a>'
            story += [Paragraph(link, _H3), *(Paragraph(escape(line) or "&nbsp;", _BODY) for line in lines)]
    return story


def _template_of(picture: _PicturePage) -> str:
    """Which page a picture goes on: a landscape one for a picture wider than it is tall."""
    return "wide" if picture.size is not None and picture.size[0] > picture.size[1] else "body"


def _words_line(layout: Layout, language: str) -> list[Flowable]:
    """The sentence that says every word the computer read is printed, with how many (none when nothing was read)."""
    count = layout.coverage.words_printed
    return [Paragraph(escape(word(language, "words_printed", count=count)), _LEAD)] if count else []


def build_pdf(
    layout: Layout,
    pictures: Iterable[tuple[int, bytes | None]],
    *,
    date_order: str,
    language: str,
    received: dict[int, date],
    prepared_on: date | None = None,
    case_label: str | None = None,
) -> bytes:
    """`pictures` are (page number, image bytes) in page order; a page with no picture shows a "not available" line.
    `case_label` is the name the case already shows to its members; the cover prints it when there is one."""
    ensure_complete(layout)
    width = A4[0] - 2 * _MARGIN_X
    title = word(language, "title")
    letters = appendix_letters(layout)
    rows = summary(layout, date_order=date_order, language=language)

    def appendix(key: str, text: str, mark: str) -> _Heading:
        heading = f"{word(language, 'appendix', letter=letters[key])} {text}"
        return _Heading(escape(heading), _H1, key=mark, title=heading, running=heading)

    toc = TableOfContents()
    toc.levelStyles = [_TOC0, _TOC1]
    toc.dotsMinLevel = 0
    story: list[Flowable] = _cover_story(layout, language, width, prepared_on, date_order, case_label)
    story += [NextPageTemplate("body"), PageBreak()]

    contents = word(language, "contents")
    story.append(_Heading(escape(contents), _H1, key="contents", title=contents, toc=False, running=contents))
    story += [toc, PageBreak()]

    about = word(language, "about")
    story += [
        _Heading(escape(about), _H1, key="about", title=about, toc=False, running=about),
        Paragraph(escape(word(language, "purpose")), _LEAD),
        Paragraph(escape(word(language, "how_to_use")), _LEAD),
        _Heading(escape(word(language, "coverage")), _H2, rule=True),
        _coverage_table(layout, language, width),
        *_words_line(layout, language),
        _Heading(escape(word(language, "holds")), _H2, rule=True),
        _summary_table(rows, language, width),
    ]

    for (number, section), row in zip(numbered(layout), rows, strict=True):
        heading = f"{number}. {row.label}"
        stats = f"{word(language, 'col_entries')}: {row.entries} · {word(language, 'col_pages')}: {row.pages}"
        if row.date_from:
            stats += f" · {row.date_from} – {row.date_to}"
        story += [
            PageBreak(),
            _Heading(escape(heading), _H1, key=f"sec-{number}", title=heading, running=heading),
            Paragraph(escape(stats), _META),
        ]
        for y, year in enumerate(by_year(section.entries)):
            year_text = str(year.year) if year.year is not None else word(language, "undated")
            story += [
                CondPageBreak(45 * mm),
                _Heading(escape(year_text), _H2, key=f"sec-{number}-y{y}", title=year_text, level=1, rule=True),
            ]
            for month in year.months:
                if month.month is not None:
                    story += [
                        CondPageBreak(35 * mm),
                        Paragraph(escape(month_name(language, month.month).upper()), _H3),
                    ]
                story.extend(
                    _entry_table(e, width=width, date_order=date_order, language=language, received=received)
                    for e in month.entries
                )

    if layout.no_entry_pages:
        story += [PageBreak(), appendix("no_entries", word(language, "no_entry_pages"), "app-no-entries")]
        story += [Paragraph(escape(word(language, "no_entry_lead")), _LEAD), _links(layout.no_entry_pages)]
        story += _page_words(layout, layout.no_entry_pages, language)
    if layout.unread_pages:
        story += [PageBreak(), appendix("unread", word(language, "unread_pages"), "app-unread")]
        story.append(_links(layout.unread_pages))
        story += _page_words(layout, layout.unread_pages, language)
    # The pictures: the heading shares its page with picture 1; a picture wider than it is tall has a landscape page.
    shown = [
        _PicturePage(
            number, prepare_picture(data), word(language, "page", number=number), word(language, "picture_missing")
        )
        for number, data in pictures
    ]
    story += [
        NextPageTemplate(_template_of(shown[0]) if shown else "body"),
        PageBreak(),
        appendix("pictures", word(language, "pictures"), "app-pictures"),
        Spacer(1, 4),
    ]
    for at, picture in enumerate(shown):
        if at:
            story += [NextPageTemplate(_template_of(picture)), PageBreak()]
        story.append(picture)

    out = io.BytesIO()
    doc = _Doc(
        out,
        pagesize=A4,
        invariant=1,
        leftMargin=_MARGIN_X,
        rightMargin=_MARGIN_X,
        topMargin=_MARGIN_Y,
        bottomMargin=_MARGIN_Y,
        title=title,
    )

    def band(canvas: Canvas, _doc: Any) -> None:
        canvas.saveState()
        canvas.setFillColor(_FOREST)
        canvas.rect(0, A4[1] - _BAND, A4[0], _BAND, stroke=0, fill=1)
        canvas.setFillColor(white)
        canvas.setFont(_SANS_BOLD, 36)
        canvas.drawString(_MARGIN_X, A4[1] - 32 * mm, title)
        canvas.setFillColor(_MINT)
        canvas.setFont(_SANS, 13)
        canvas.drawString(_MARGIN_X, A4[1] - 44 * mm, word(language, "subtitle"))
        canvas.setFont(_SANS_BOLD, 13)
        canvas.drawString(_MARGIN_X, A4[1] - _BAND + 10 * mm, word(language, "prepared_by"))
        # The foot: a thin rule, how to use the file, and the mark.
        canvas.setStrokeColor(_LINE)
        canvas.setLineWidth(0.75)
        canvas.line(_MARGIN_X, _COVER_FOOT - 4 * mm, A4[0] - _MARGIN_X, _COVER_FOOT - 4 * mm)
        use = Paragraph(escape(word(language, "how_to_use")), _FOOT_USE)
        _w, h = use.wrap(width, 30 * mm)
        use.drawOn(canvas, _MARGIN_X, _COVER_FOOT - 4 * mm - 3 * mm - h)
        canvas.setFillColor(_FOREST)
        canvas.setFont(_SANS_BOLD, 16)
        canvas.drawString(_MARGIN_X, _MARGIN_Y - 4 * mm, word(language, "brand"))
        canvas.restoreState()

    def footer(canvas: Canvas, d: _Doc, page_width: float) -> None:
        canvas.setStrokeColor(_LINE)
        canvas.setLineWidth(0.5)
        canvas.line(_MARGIN_X, 15 * mm, page_width - _MARGIN_X, 15 * mm)
        canvas.setFont(_SANS, 8.5)
        canvas.setFillColor(_AMBER_TEXT)
        canvas.drawString(_MARGIN_X, 10.5 * mm, word(language, "not_checked_short"))
        canvas.setFillColor(_SLATE)
        canvas.drawRightString(
            page_width - _MARGIN_X,
            10.5 * mm,
            word(language, "page_x_of_y", page=canvas.getPageNumber(), total=d.total_pages),
        )

    def running_header_for(size: tuple[float, float]) -> Any:
        def running_header(canvas: Canvas, d: _Doc) -> None:
            canvas.saveState()
            footer(canvas, d, size[0])
            y = size[1] - 14 * mm
            canvas.setFont(_SANS, 8.5)
            canvas.setFillColor(_SLATE)
            canvas.drawString(_MARGIN_X, y, title)
            canvas.drawRightString(size[0] - _MARGIN_X, y, d.section_text)
            canvas.setStrokeColor(_LINE)
            canvas.setLineWidth(0.5)
            canvas.line(_MARGIN_X, y - 3 * mm, size[0] - _MARGIN_X, y - 3 * mm)
            canvas.restoreState()

        return running_header

    cover_frame = Frame(
        _MARGIN_X, _COVER_FOOT, width, A4[1] - _BAND - _COVER_FOOT - 12 * mm, id="cover", leftPadding=0, rightPadding=0
    )
    body_frame = Frame(_MARGIN_X, _MARGIN_Y, width, A4[1] - 2 * _MARGIN_Y, id="body", leftPadding=0, rightPadding=0)
    wide = landscape(A4)
    wide_frame = Frame(
        _MARGIN_X, _MARGIN_Y, wide[0] - 2 * _MARGIN_X, wide[1] - 2 * _MARGIN_Y, id="wide", leftPadding=0, rightPadding=0
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[cover_frame], onPage=band),
            PageTemplate(id="body", frames=[body_frame], onPageEnd=running_header_for(A4)),
            PageTemplate(id="wide", frames=[wide_frame], onPageEnd=running_header_for(wide), pagesize=wide),
        ]
    )
    doc.multiBuild([_PageTotal(doc), *story])
    return out.getvalue()
