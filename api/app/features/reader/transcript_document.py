"""How the records transcript is arranged for reading, shared by the PDF and the Word copy (ADR 0035).

Only arranges data: numbered sections, entries grouped by year and month, the summary table and the appendix
letters. It draws nothing, adds no words of the record (rule 1) and logs nothing (rule 6). Labels come from the
pack's keys (rule 11).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.features.reader.transcript import TranscriptEntry
from app.features.reader.transcript_layout import Layout, Section
from app.features.reader.transcript_words import label_text, show_date


@dataclass(frozen=True)
class Month:
    month: int | None  # None when the year is None
    entries: list[TranscriptEntry]


@dataclass(frozen=True)
class Year:
    year: int | None  # None = "No date", always last
    months: list[Month]


@dataclass(frozen=True)
class SummaryRow:
    number: int  # 1-based section number
    label: str  # plain words
    entries: int
    pages: int
    date_from: str  # "" when the section has no dated entry
    date_to: str


def numbered(layout: Layout) -> list[tuple[int, Section]]:
    return list(enumerate(layout.sections, start=1))


def by_year(entries: list[TranscriptEntry]) -> list[Year]:
    """Groups consecutive runs by year and then month, keeping the given order (date order, undated last)."""
    years: list[tuple[int | None, list[tuple[int | None, list[TranscriptEntry]]]]] = []
    for e in entries:
        year = e.event_date.year if e.event_date else None
        month = e.event_date.month if e.event_date else None
        if not years or years[-1][0] != year:
            years.append((year, []))
        months = years[-1][1]
        if not months or months[-1][0] != month:
            months.append((month, []))
        months[-1][1].append(e)
    return [Year(y, [Month(m, es) for m, es in months]) for y, months in years]


def summary(layout: Layout, *, date_order: str, language: str) -> list[SummaryRow]:
    rows: list[SummaryRow] = []
    for number, section in numbered(layout):
        dated = [e.event_date for e in section.entries if e.event_date is not None]
        rows.append(
            SummaryRow(
                number,
                label_text(section.label),
                len(section.entries),
                len({e.page_no for e in section.entries}),
                show_date(min(dated), date_order, language) if dated else "",
                show_date(max(dated), date_order, language) if dated else "",
            )
        )
    return rows


def appendix_letters(layout: Layout) -> dict[str, str]:
    """Appendix letters in order: no entries, unread, then the pictures (which always exist)."""
    present = [
        key
        for key, there in (
            ("no_entries", bool(layout.no_entry_pages)),
            ("unread", bool(layout.unread_pages)),
            ("pictures", True),
        )
        if there
    ]
    return {key: chr(ord("A") + i) for i, key in enumerate(present)}
