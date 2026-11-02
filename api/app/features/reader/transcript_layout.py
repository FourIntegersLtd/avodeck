"""How the records transcript is arranged for the page and for the Word and PDF files (ADR 0035).

Entries go into the pack's sections (`transcript.yaml`), each in date order; one rule (`section_of`) serves the
page, the Word file and the PDF. An entry no section claims goes in a final "other" section, so nothing is left
out. Nothing here is logged (rule 6) and nothing is added to the words of the record (rule 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.packs import Pack
from app.features.reader.transcript import TranscriptEntry

OTHER_KEY = "other"
OTHER_LABEL = "transcript.section.other_entries"
ALL_KEY = "entries"
ALL_LABEL = "transcript.section.entries"


@dataclass(frozen=True)
class Section:
    key: str
    label: str  # an i18n key; the files print it with `label_text()`
    entries: list[TranscriptEntry] = field(default_factory=list, repr=False)


@dataclass(frozen=True)
class Coverage:
    total_pages: int  # pages that are not set aside
    with_entries: int
    no_entries: int
    unreadable: int
    set_aside: int  # copies, replaced and quarantined pages


@dataclass(frozen=True)
class Layout:
    sections: list[Section]
    no_entry_pages: list[int]
    unread_pages: list[int]
    coverage: Coverage


def section_of(entry: TranscriptEntry, pack: Pack) -> tuple[int, str, str]:
    """(position, key, label) of the first pack section the entry matches, else the "other" section placed last.

    A section matches when each condition it gives holds: `sources` contains the entry's page source,
    `letters` equals whether the entry is the pack's letter kind, `administrative` equals whether it is folded."""
    raise NotImplementedError


def arrange(
    entries: list[TranscriptEntry],
    pack: Pack,
    *,
    no_entry_pages: list[int],
    unread_pages: list[int],
    coverage: Coverage,
) -> Layout:
    """Sections in pack order (empty ones dropped), the date order of `entries` kept inside each."""
    raise NotImplementedError
