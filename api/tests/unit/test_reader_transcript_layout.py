"""The transcript layout (ADR 0035): which section an entry is in, and the sections in order.

Entries are built by hand and the pack is a small fake, so nothing here needs a database.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

from app.core.packs import MergedPack as Pack
from app.features.reader.transcript import TranscriptEntry
from app.features.reader.transcript_layout import (
    ALL_KEY,
    OTHER_KEY,
    Coverage,
    arrange,
    section_of,
)

SECTIONS: list[dict[str, Any]] = [
    {"key": "own", "label": "t.own", "sources": ["own_src", "alt_src"], "letters": False, "administrative": False},
    {"key": "letters", "label": "t.letters", "letters": True, "administrative": False},
    {"key": "elsewhere", "label": "t.elsewhere", "administrative": False},
    {"key": "admin", "label": "t.admin", "administrative": True},
]
COVERAGE = Coverage(total_pages=0, with_entries=0, no_entries=0, unreadable=0, set_aside=0)


def pack(sections: list[dict[str, Any]] | None = None) -> Pack:
    fake = SimpleNamespace(
        transcript_sections=lambda: SECTIONS if sections is None else sections, letter_entry_kind=lambda: "letter"
    )
    return cast(Pack, fake)


def entry(
    page: int, *, kind: str = "note", source: str | None = "own_src", folded: bool = False, day: date | None = None
) -> TranscriptEntry:
    return TranscriptEntry(
        uuid.uuid4(),
        page,
        kind,
        kind,
        folded,
        None,
        None,
        None,
        day,
        None,
        [f"line {page}"],
        [],
        [],
        source_type=source,
    )


def keys(entries: list[TranscriptEntry], p: Pack | None = None) -> list[str]:
    return [section_of(e, p or pack())[1] for e in entries]


def test_entries_go_to_the_first_matching_section() -> None:
    got = keys(
        [
            entry(1),
            entry(2, kind="letter", source="hospital"),
            entry(3, source="hospital"),
            entry(4, kind="booking", source="own_src", folded=True),
        ]
    )
    assert got == ["own", "letters", "elsewhere", "admin"]


def test_a_letter_inside_the_own_record_goes_to_letters() -> None:
    assert keys([entry(1, kind="letter", source="own_src")]) == ["letters"]


def test_a_section_that_names_sources_never_matches_an_entry_without_one() -> None:
    assert keys([entry(1, source=None)]) == ["elsewhere"]


def test_section_of_gives_the_position_key_and_label() -> None:
    assert section_of(entry(1, kind="letter"), pack()) == (1, "letters", "t.letters")


def test_nothing_is_left_out_and_unmatched_go_to_other_last() -> None:
    only_own = [{"key": "own", "label": "t.own", "sources": ["own_src"]}]
    items = [entry(1), entry(2, source="x"), entry(3, source=None, kind="odd")]
    layout = arrange(items, pack(only_own), no_entry_pages=[], unread_pages=[], coverage=COVERAGE)
    assert [s.key for s in layout.sections] == ["own", OTHER_KEY]
    assert sum(len(s.entries) for s in layout.sections) == len(items)
    assert section_of(items[1], pack(only_own))[0] == 1


def test_date_order_within_a_section() -> None:
    items = [entry(5, day=date(2019, 1, 1)), entry(2, day=date(2020, 1, 1)), entry(9, source="x"), entry(1)]
    layout = arrange(items, pack(), no_entry_pages=[], unread_pages=[], coverage=COVERAGE)
    own = next(s for s in layout.sections if s.key == "own")
    assert [e.page_no for e in own.entries] == [5, 2, 1]


def test_empty_sections_are_dropped() -> None:
    layout = arrange([entry(1)], pack(), no_entry_pages=[3], unread_pages=[4], coverage=COVERAGE)
    assert [s.key for s in layout.sections] == ["own"]
    assert layout.no_entry_pages == [3] and layout.unread_pages == [4] and layout.coverage == COVERAGE


def test_no_sections_in_the_pack_gives_one_section() -> None:
    items = [entry(1), entry(2, kind="letter")]
    layout = arrange(items, pack([]), no_entry_pages=[], unread_pages=[], coverage=COVERAGE)
    assert [s.key for s in layout.sections] == [ALL_KEY]
    assert layout.sections[0].entries == items
    assert section_of(items[0], pack([])) == (0, ALL_KEY, "transcript.section.entries")


def test_an_administrative_entry_keeps_all_its_lines() -> None:
    e = TranscriptEntry(
        uuid.uuid4(), 1, "booking", "booking", True, None, None, None, None, None, ["a b c", "d e f"], [], []
    )
    layout = arrange([e], pack(), no_entry_pages=[], unread_pages=[], coverage=COVERAGE)
    assert layout.sections[0].entries[0].lines == ["a b c", "d e f"]
