"""The T-04 word check (rule 2): a suggestion is kept only when the letter's own words back it.

- A name is stored as the letter's own words (the run the model cited), never the model's value, so a
  different spelling or case is corrected to the letter's, and words the identifier strip replaced with
  a placeholder (e.g. `[NAME_1]`) are never kept.
- A key (party role, instructing party type, court track) must be one of the pack's keys AND the cited
  words must carry that key's word from the pack (its label), so a key the letter never names is refused.

Words are read from the packs (rule 11); the test runs under the launch and the fixture packs.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.packs import MergedPack, load_library
from app.features.setup.jobs import check_suggestion
from app.features.setup.letter import Letter, Word
from tests.support.packs import BOTH, FIXTURE_ROOT, LAUNCH_ROOT, Combo

NAME = ["Harland", "&", "Price", "LLP"]


def pack_for(combo: Combo) -> MergedPack:
    return load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*combo.codes)


def letter_of(*words: str) -> Letter:
    return Letter([Word(f"w{i}", w, i == 0) for i, w in enumerate(words)])


def item(field: str, value: str, ids: list[str]) -> dict[str, Any]:
    return {"field": field, "value": value, "word_ids": ids, "reason": "letter"}


def ids(start: int, count: int) -> list[str]:
    return [f"w{i}" for i in range(start, start + count)]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_name_is_stored_in_the_letters_own_words(combo: Combo) -> None:
    pack = pack_for(combo)
    letter = letter_of("From", *NAME, "today")
    kept = check_suggestion(letter, pack, item("instructing_party_name", "harland & price llp", ids(1, 4)))
    assert kept is not None
    assert kept == (ids(1, 4), " ".join(NAME))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_name_hidden_by_the_identifier_strip_is_never_kept(combo: Combo) -> None:
    pack = pack_for(combo)
    letter = letter_of("From", "Margaret", "today")
    letter.masked["w1"] = "[NAME_1]"
    assert check_suggestion(letter, pack, item("instructing_party_name", "[NAME_1]", ["w1"])) is None
    letter = letter_of("From", "Ms", "Margaret", "today")
    letter.masked["w2"] = "[NAME_1]"
    assert check_suggestion(letter, pack, item("instructing_party_name", "Ms [NAME_1]", ["w1", "w2"])) is None


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_party_role_must_be_named_by_the_cited_words(combo: Combo) -> None:
    pack = pack_for(combo)
    roles = pack.party_roles()
    first, second = roles[0], roles[1]
    letter = letter_of("We", "act", "for", "the", first["label"].title() + ",", "and", "the", second["label"])
    assert check_suggestion(letter, pack, item("party_role", first["key"], ids(0, 5))) == (ids(0, 5), first["key"])
    assert check_suggestion(letter, pack, item("party_role", second["key"], ids(0, 5))) is None
    assert check_suggestion(letter, pack, item("party_role", second["key"], ids(6, 2))) == (
        ids(6, 2),
        second["key"],
    )
    assert check_suggestion(letter, pack, item("party_role", "not_a_key", ids(0, 5))) is None


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_an_instructing_party_type_must_be_named_by_the_cited_words(combo: Combo) -> None:
    pack = pack_for(combo)
    types = pack.instructing_party_types()
    first = types[0]
    plural = first["label"].title() + "s"  # a letterhead often writes the word in the plural
    letter = letter_of(*NAME, plural, "and", "Partners")
    assert check_suggestion(letter, pack, item("instructing_party_type", first["key"], ["w4"])) == (
        ["w4"],
        first["key"],
    )
    assert check_suggestion(letter, pack, item("instructing_party_type", first["key"], ["w6"])) is None
    assert check_suggestion(letter, pack, item("instructing_party_type", first["key"], ids(0, 4))) is None


def test_a_court_track_must_be_named_by_the_cited_words() -> None:
    pack = pack_for(BOTH[0])
    tracks = list(pack.prompt_context("T-04")["court_tracks"])
    assert len(tracks) >= 2
    first, second = tracks[0], tracks[1]
    letter = letter_of("allocated", "to", "the", *first.split("_"), "track")
    span = ids(0, 4 + len(first.split("_")))
    assert check_suggestion(letter, pack, item("court_track", first, span)) == (span, first)
    assert check_suggestion(letter, pack, item("court_track", second, span)) is None
