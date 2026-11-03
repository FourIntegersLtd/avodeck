"""The Word copy of the records transcript (ADR 0035): the layout as headings and paragraphs, no pictures."""

from __future__ import annotations

import io
from datetime import date

from docx import Document

from app.features.reader.transcript import TranscriptEntry
from app.features.reader.transcript_layout import Layout
from app.features.reader.transcript_words import label_text, show_date, word


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


def build(
    layout: Layout, *, date_order: str, language: str, received: dict[int, date], prepared_on: date | None = None
) -> bytes:
    """`received` maps a page number to the date its letter was received, for letters only."""
    doc = Document()
    doc.add_heading(word(language, "title"), level=0)
    doc.add_paragraph(word(language, "not_checked"))
    cov = layout.coverage
    doc.add_heading(word(language, "coverage"), level=1)
    doc.add_paragraph(
        word(
            language,
            "coverage_line",
            total=cov.total_pages,
            with_entries=cov.with_entries,
            no_entries=cov.no_entries,
            unreadable=cov.unreadable,
            set_aside=cov.set_aside,
        )
    )
    for section in layout.sections:
        doc.add_heading(label_text(section.label), level=1)
        for entry in section.entries:
            parts = entry_heading(entry, date_order=date_order, language=language, received=received)
            doc.add_heading(", ".join(parts), level=2)
            for line in entry.lines:
                doc.add_paragraph(line)
    if layout.no_entry_pages:
        doc.add_heading(word(language, "no_entry_pages"), level=1)
        doc.add_paragraph(word(language, "no_entry_lead"))
        doc.add_paragraph(", ".join(str(n) for n in layout.no_entry_pages))
    if layout.unread_pages:
        doc.add_heading(word(language, "unread_pages"), level=1)
        doc.add_paragraph(", ".join(str(n) for n in layout.unread_pages))
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
