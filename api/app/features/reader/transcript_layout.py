"""How the records transcript is arranged for the page and for the Word and PDF files (ADR 0035).

Entries go into the pack's sections (`transcript.yaml`), each in date order; one rule (`section_of`) serves the
page, the Word file and the PDF. An entry no section claims goes in a final "other" section, so nothing is left
out. Nothing here is logged (rule 6) and nothing is added to the words of the record (rule 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.packs import MergedPack as Pack
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


def _sections(pack: Pack) -> list[dict[str, Any]]:
    # `transcript_sections` is the pack's `transcript.yaml`; a pack without it has no sections.
    return list(getattr(pack, "transcript_sections", lambda: [])())


def place(pack: Pack, *, source_type: str | None, kind: str, folded: bool) -> tuple[int, str, str]:
    """The rule behind `section_of`, from the three things it needs, so the page can apply it unopened."""
    sections = _sections(pack)
    if not sections:
        return 0, ALL_KEY, ALL_LABEL
    letter = kind == pack.letter_entry_kind()
    for position, section in enumerate(sections):
        if "sources" in section and source_type not in section["sources"]:
            continue
        if "letters" in section and bool(section["letters"]) != letter:
            continue
        if "administrative" in section and bool(section["administrative"]) != folded:
            continue
        return position, str(section["key"]), str(section["label"])
    return len(sections), OTHER_KEY, OTHER_LABEL


def section_of(entry: TranscriptEntry, pack: Pack) -> tuple[int, str, str]:
    """(position, key, label) of the first pack section the entry matches, else the "other" section placed last.

    A section matches when each condition it gives holds: `sources` contains the entry's page source,
    `letters` equals whether the entry is the pack's letter kind, `administrative` equals whether it is folded."""
    return place(pack, source_type=entry.source_type, kind=entry.kind, folded=entry.folded)


def arrange(
    entries: list[TranscriptEntry],
    pack: Pack,
    *,
    no_entry_pages: list[int],
    unread_pages: list[int],
    coverage: Coverage,
) -> Layout:
    """Sections in pack order (empty ones dropped), the date order of `entries` kept inside each."""
    found: dict[int, Section] = {}
    for e in entries:
        position, key, label = section_of(e, pack)
        found.setdefault(position, Section(key, label)).entries.append(e)
    return Layout([found[p] for p in sorted(found)], no_entry_pages, unread_pages, coverage)
