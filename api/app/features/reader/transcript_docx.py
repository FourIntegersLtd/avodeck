"""The Word copy of the records transcript (ADR 0035): the layout as headings and paragraphs, no pictures.

Order: cover, contents (internal links), about this transcript, numbered sections (year, month, entries), then the
appendices. Word paginates by itself, so the contents carry no page numbers; the footer counts pages with fields.
"""

from __future__ import annotations

import io
import re
from datetime import date

from docx import Document
from docx.document import Document as DocumentType
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from app.features.reader.transcript import TranscriptEntry
from app.features.reader.transcript_document import appendix_letters, by_year, ensure_complete, numbered, summary
from app.features.reader.transcript_layout import Layout
from app.features.reader.transcript_words import label_text, month_name, show_date, word

_FOREST = RGBColor(0x0F, 0x3D, 0x2C)
_SLATE = RGBColor(0x4F, 0x59, 0x53)
_AMBER = RGBColor(0x8A, 0x3F, 0x06)
_HEADING_FONT = "Atkinson Hyperlegible"  # Word falls back to its own font if this is not installed
_BODY_FONT = "Georgia"
_MONO_FONT = "Consolas"
_GREY = RGBColor(0x6B, 0x74, 0x6E)  # words in no entry: grey and smaller
_TEXT_WIDTH = Cm(17)  # A4 less 2 cm margins


def entry_heading(entry: TranscriptEntry, *, date_order: str, language: str, received: dict[int, date]) -> list[str]:
    """The parts of an entry's heading, in order; the first is always the page."""
    parts = [
        word(language, "page", number=entry.page_no),
        show_date(entry.event_date, date_order, language),
        label_text(entry.kind_label),
    ]
    if entry.author_label:
        parts.append(label_text(entry.author_label))
    if entry.specialty_label:
        parts.append(label_text(entry.specialty_label))
    if entry.page_no in received:
        parts.append(word(language, "received", date=show_date(received[entry.page_no], date_order, language)))
    return parts


def _style_document(doc: DocumentType) -> None:
    styles = doc.styles
    for style_name, size, color in (
        ("Title", 28, _FOREST),
        ("Heading 1", 18, _FOREST),
        ("Heading 2", 14, _FOREST),
        ("Heading 3", 11, _SLATE),
        ("Heading 4", 11, _SLATE),
    ):
        style = styles[style_name]
        style.font.name = _HEADING_FONT
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = color
        # The built-in styles name a theme font that would override the name set above.
        rfonts = style.element.get_or_add_rPr().get_or_add_rFonts()
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            rfonts.attrib.pop(qn(attr), None)
    styles["Heading 1"].paragraph_format.page_break_before = True
    normal = styles["Normal"]
    normal.font.name = _BODY_FONT
    normal.font.size = Pt(11)
    normal.paragraph_format.line_spacing = 1.15
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = section.top_margin = section.bottom_margin = Cm(2)


def _run(
    paragraph: Paragraph,
    text: str,
    *,
    mono: bool = False,
    font: str | None = None,
    color: RGBColor | None = None,
    size: float | None = None,
) -> Run:
    run = paragraph.add_run(text)
    if mono:
        run.font.name = _MONO_FONT
    elif font:
        run.font.name = font
    if color is not None:
        run.font.color.rgb = color
    if size is not None:
        run.font.size = Pt(size)
    return run


def _bookmark(paragraph: Paragraph, name: str, ident: int) -> None:
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(ident))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(ident))
    paragraph._p.insert(1 if paragraph._p.pPr is not None else 0, start)
    paragraph._p.append(end)


def _link(paragraph: Paragraph, text: str, anchor: str) -> None:
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), anchor)
    link.set(qn("w:history"), "1")
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    colour = OxmlElement("w:color")
    colour.set(qn("w:val"), "1E6B4E")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(colour)
    props.append(underline)
    run.append(props)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def _field(paragraph: Paragraph, instruction: str) -> None:
    """A simple field (page number, page count) that Word fills in when it lays the pages out."""
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), instruction)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), "17")
    props.append(size)
    run.append(props)
    t = OxmlElement("w:t")
    t.text = "1"
    run.append(t)
    field.append(run)
    paragraph._p.append(field)


def _header_footer(doc: DocumentType, language: str) -> None:
    section = doc.sections[0]
    section.different_first_page_header_footer = True  # the cover carries neither
    section.first_page_header.paragraphs[0].text = ""
    section.first_page_footer.paragraphs[0].text = ""
    head = section.header.paragraphs[0]
    head.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _run(head, word(language, "title"), font=_HEADING_FONT, color=_SLATE, size=8.5)
    foot = section.footer.paragraphs[0]
    foot.paragraph_format.tab_stops.add_tab_stop(_TEXT_WIDTH, WD_TAB_ALIGNMENT.RIGHT)
    _run(foot, word(language, "not_checked_short"), font=_HEADING_FONT, color=_AMBER, size=8.5)
    _run(foot, "\t", size=8.5)
    # "Page {PAGE} of {NUMPAGES}" from the shared template, with Word counting the pages itself.
    template = word(language, "page_x_of_y", page="{P}", total="{N}")
    for part in re.split(r"(\{[PN]\})", template):
        if part == "{P}":
            _field(foot, " PAGE ")
        elif part == "{N}":
            _field(foot, " NUMPAGES ")
        elif part:
            _run(foot, part, font=_HEADING_FONT, color=_SLATE, size=8.5)


def _table(doc: DocumentType, rows: list[list[str]], *, header: bool, right: set[int]) -> None:
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    names = {style.name for style in doc.styles}
    table.style = "Light Grid Accent 1" if "Light Grid Accent 1" in names else "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            para = table.cell(r, c).paragraphs[0]
            if c in right:
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = para.add_run(value)
            is_header = header and r == 0
            run.bold = is_header
            if c in right and not is_header:
                run.font.name = _MONO_FONT
    doc.add_paragraph()


def _heading(doc: DocumentType, text: str, level: int, bookmark: tuple[str, int] | None = None) -> Paragraph:
    para: Paragraph = doc.add_heading(text, level=level)
    if bookmark:
        _bookmark(para, *bookmark)
    return para


def _entry(
    doc: DocumentType,
    entry: TranscriptEntry,
    *,
    date_order: str,
    language: str,
    received: dict[int, date],
    level: int = 4,
) -> None:
    parts = entry_heading(entry, date_order=date_order, language=language, received=received)
    para = doc.add_paragraph(style=f"Heading {level}")
    for i, part in enumerate(parts):
        if i:
            _run(para, ", ")
        _run(para, part, mono=i < 2)  # the page and the date are set in a fixed-width face
    for lines, grey in ((entry.before, True), (entry.lines, False), (entry.after, True)):
        if grey and lines:  # the words of the page that lie in no entry: a small grey label, then small grey lines
            _run(doc.add_paragraph(), word(language, "also_on_page"), font=_HEADING_FONT, color=_GREY, size=9)
        for line in lines:
            if grey:
                _run(doc.add_paragraph(), line, color=_GREY, size=9.5)
            else:
                doc.add_paragraph(line)


def _cover(
    doc: DocumentType,
    layout: Layout,
    *,
    date_order: str,
    language: str,
    prepared_on: date | None,
    case_label: str | None,
) -> None:
    """The title page, in the order of the PDF's: the not-checked notice and the bundle page count stay the second
    and third paragraphs; the case, date, counts, subtitle, who prepared it and how to use it follow."""
    cov = layout.coverage
    doc.add_heading(word(language, "title"), level=0)
    doc.add_paragraph(word(language, "not_checked"))  # always the second paragraph, on the first page
    _run(doc.add_paragraph(), word(language, "bundle_pages", count=cov.bundle_pages), font=_HEADING_FONT, size=14)
    if prepared_on is not None:
        shown = show_date(prepared_on, date_order, language)
        _run(doc.add_paragraph(), word(language, "prepared", date=shown), font=_HEADING_FONT, size=14)
    if case_label:
        line = word(language, "case_line", label=case_label)
        _run(doc.add_paragraph(), line, font=_HEADING_FONT, size=14).bold = True
    counts = f"{word(language, 'cov_with')}: {cov.with_entries} · {word(language, 'cov_unread')}: {cov.unreadable}"
    _run(doc.add_paragraph(), counts, font=_HEADING_FONT, color=_FOREST, size=12)
    _run(doc.add_paragraph(), word(language, "subtitle"), color=_SLATE)
    _run(doc.add_paragraph(), word(language, "prepared_by"), font=_HEADING_FONT, color=_FOREST, size=12).bold = True
    doc.add_page_break()  # type: ignore[no-untyped-call]


def _about(doc: DocumentType, layout: Layout, *, date_order: str, language: str) -> None:
    cov = layout.coverage
    _heading(doc, word(language, "about"), 1)
    doc.add_paragraph(word(language, "purpose"))
    doc.add_paragraph(word(language, "how_to_use"))
    _heading(doc, word(language, "coverage"), 2)
    _table(
        doc,
        [
            [word(language, "cov_total"), str(cov.bundle_pages)],
            [word(language, "cov_set_aside"), str(cov.set_aside)],
            [word(language, "cov_in_transcript"), str(cov.total_pages)],
            [word(language, "cov_with"), str(cov.with_entries)],
            [word(language, "cov_none"), str(cov.no_entries)],
            [word(language, "cov_unread"), str(cov.unreadable)],
        ],
        header=False,
        right={1},
    )
    if cov.words_printed:
        doc.add_paragraph(word(language, "words_printed", count=cov.words_printed))
    _heading(doc, word(language, "holds"), 2)
    columns = ("col_number", "col_section", "col_entries", "col_pages", "col_from", "col_to")
    rows = summary(layout, date_order=date_order, language=language)
    _table(
        doc,
        [
            [word(language, k) for k in columns],
            *[[str(r.number), r.label, str(r.entries), str(r.pages), r.date_from, r.date_to] for r in rows],
        ],
        header=True,
        right={0, 2, 3},
    )


def build(
    layout: Layout,
    *,
    date_order: str,
    language: str,
    received: dict[int, date],
    prepared_on: date | None = None,
    case_label: str | None = None,
) -> bytes:
    """`received` maps a page number to the date its letter was received, for letters only. `case_label` is the
    name the case already shows to its members; the cover prints it when there is one."""
    ensure_complete(layout)
    doc = Document()
    _style_document(doc)
    _header_footer(doc, language)
    _cover(doc, layout, date_order=date_order, language=language, prepared_on=prepared_on, case_label=case_label)

    letters = appendix_letters(layout)
    appendices: list[tuple[str, str, str]] = []  # (key, anchor, heading text)
    for key, there, title in (
        ("no_entries", bool(layout.no_entry_pages), word(language, "no_entry_pages")),
        ("unread", bool(layout.unread_pages), word(language, "unread_pages")),
    ):
        if there:
            heading = f"{word(language, 'appendix', letter=letters[key])}  {title}"
            appendices.append((key, f"app_{key}", heading))
    sections = [(f"sec_{n}", f"{n}. {label_text(sec.label)}") for n, sec in numbered(layout)]

    # Contents: a plain bold paragraph (not a heading), then one internal link per section and appendix.
    _run(doc.add_paragraph(), word(language, "contents")).bold = True
    for anchor, text in [*sections, *((a, h) for _, a, h in appendices)]:
        _link(doc.add_paragraph(), text, anchor)

    _about(doc, layout, date_order=date_order, language=language)

    ident = 0
    rows = summary(layout, date_order=date_order, language=language)
    for (anchor, text), (_, section), row in zip(sections, numbered(layout), rows, strict=True):
        ident += 1
        _heading(doc, text, 1, (anchor, ident))
        span = f" · {row.date_from} – {row.date_to}" if row.date_from else ""
        line = f"{word(language, 'col_entries')}: {row.entries} · {word(language, 'col_pages')}: {row.pages}{span}"
        _run(doc.add_paragraph(), line, color=_SLATE)
        for year in by_year(section.entries):
            _heading(doc, str(year.year) if year.year is not None else word(language, "undated"), 2)
            for month in year.months:
                if month.month is not None:
                    _heading(doc, month_name(language, month.month), 3)
                for entry in month.entries:
                    # Under a month the entries are level 4; with no month heading they follow the year at level 3.
                    level = 4 if month.month is not None else 3
                    _entry(doc, entry, date_order=date_order, language=language, received=received, level=level)

    for key, anchor, heading in appendices:
        ident += 1
        _heading(doc, heading, 1, (anchor, ident))
        if key == "no_entries":
            doc.add_paragraph(word(language, "no_entry_lead"))
        pages = layout.no_entry_pages if key == "no_entries" else layout.unread_pages
        if key == "no_entries":  # the words the computer read on each page with no entries, under its number
            for number in pages:
                if layout.picture_words.get(number):
                    _run(doc.add_paragraph(), word(language, "page", number=number), font=_HEADING_FONT).bold = True
                    for line in layout.picture_words[number]:
                        doc.add_paragraph(line)
            pages = [n for n in pages if not layout.picture_words.get(n)]
        if pages:
            doc.add_paragraph(", ".join(str(n) for n in pages))
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
