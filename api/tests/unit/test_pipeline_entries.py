"""Slice A: T-07b page-level `entries[]` (ADR 0028): the schema has no free text, provenance refuses
entries whose word ids are unknown, out of order or overlapping, and a rejection is counted, never logged
with text. Runs under the launch and the fixture packs."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import jsonschema_rs
import pytest

from app.core.packs import MergedPack, default_library
from app.features.pipeline import dates, extract
from tests.support.packs import BOTH, Combo

WORDS = [(f"p1w{n}", f"word{n}") for n in range(1, 11)]
POSITIONS = {w: n for n, (w, _) in enumerate(WORDS, start=1)}


def pack_of(combo: Combo) -> MergedPack:
    return default_library().combine(*combo.codes)


def entry(pack: MergedPack, first: str, last: str, **over: Any) -> dict[str, Any]:
    base = {
        "kind": pack.entry_kinds()[0]["key"],
        "first_word_id": first,
        "last_word_id": last,
        "author_role": None,
        "specialty": None,
    }
    return {**base, **over}


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_schema_has_entries_built_from_the_pack_and_no_free_text(combo: Combo) -> None:
    pack = pack_of(combo)
    schema = extract.page_schema(pack)
    assert "entries" in schema["required"]
    item = schema["properties"]["entries"]["items"]
    assert set(item["properties"]) == {"kind", "first_word_id", "last_word_id", "author_role", "specialty"}
    assert item["additionalProperties"] is False
    assert item["properties"]["kind"]["enum"] == [k["key"] for k in pack.entry_kinds()]
    assert set(item["properties"]["author_role"]["enum"]) == {*(r["key"] for r in pack.author_roles()), None}
    assert set(item["properties"]["specialty"]["enum"]) == {*(s["key"] for s in pack.entry_specialties()), None}
    for name in ("first_word_id", "last_word_id"):
        assert item["properties"][name]["maxLength"] <= 40
    assert "clinician_role" in schema["properties"]["facts"]["items"]["properties"], "clinician_role is kept"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_validator_refuses_free_text_in_an_entry(combo: Combo) -> None:
    pack = pack_of(combo)
    validator = jsonschema_rs.validator_for(extract.page_schema(pack))
    page: dict[str, Any] = {
        "source_type": None,
        "printed_dates": [],
        "facts": [],
        "references_out": [],
        "availability_evidence": [],
        "actions_requested_of_clinician": [],
    }
    good = entry(pack, "p1w1", "p1w2")
    assert validator.is_valid({**page, "entries": [good]})
    assert not validator.is_valid({**page, "entries": [{**good, "summary": "a short prose summary"}]})
    assert not validator.is_valid({**page, "entries": [{**good, "kind": "not a pack kind"}]})
    assert not validator.is_valid({**page, "entries": [{**good, "author_role": "free text role"}]})
    assert not validator.is_valid({**page, "entries": [{**good, "first_word_id": "x" * 500}]})


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_good_entries_are_kept_with_their_word_numbers(combo: Combo) -> None:
    pack = pack_of(combo)
    role = pack.author_roles()[0]["key"]
    out = {"entries": [entry(pack, "p1w1", "p1w3", author_role=role), entry(pack, "p1w4", "p1w4")]}
    result = extract.sort_entries(out, POSITIONS, pack)
    assert result.rejected == 0
    assert [(e.first_n, e.last_n, e.author_role) for e in result.kept] == [(1, 3, role), (4, 4, None)]
    assert result.kept[0].first_word_id == "p1w1" and result.kept[0].last_word_id == "p1w3"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_unknown_and_reversed_entries_are_dropped_and_counted(combo: Combo) -> None:
    pack = pack_of(combo)
    out = {
        "entries": [
            entry(pack, "p1w1", "p1w3"),
            entry(pack, "p1w9", "p1w6"),  # last before first
            entry(pack, "p1w1", "p9w9"),  # unknown id
            entry(pack, "p1w4", "p1w5"),
        ]
    }
    result = extract.sort_entries(out, POSITIONS, pack)
    assert [(e.first_n, e.last_n) for e in result.kept] == [(1, 3), (4, 5)]
    assert result.rejected == 2


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_an_overlapping_entry_is_trimmed_to_start_after_the_earlier_one(combo: Combo) -> None:
    pack = pack_of(combo)
    role = pack.author_roles()[0]["key"]
    out = {"entries": [entry(pack, "p1w1", "p1w3"), entry(pack, "p1w2", "p1w5", author_role=role)]}
    result = extract.sort_entries(out, POSITIONS, pack)
    assert result.rejected == 0
    assert [(e.first_n, e.last_n) for e in result.kept] == [(1, 3), (4, 5)]
    second = result.kept[1]
    assert (second.first_word_id, second.last_word_id) == ("p1w4", "p1w5"), "the first word is the new first"
    assert second.author_role == role and second.kind == pack.entry_kinds()[0]["key"]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_an_entry_with_nothing_left_after_trimming_is_dropped_and_counted(combo: Combo) -> None:
    pack = pack_of(combo)
    out = {
        "entries": [
            entry(pack, "p1w1", "p1w6"),
            entry(pack, "p1w3", "p1w5"),  # wholly inside the first
            entry(pack, "p1w5", "p1w8"),  # trimmed to 7 to 8
        ]
    }
    result = extract.sort_entries(out, POSITIONS, pack)
    assert [(e.first_n, e.last_n) for e in result.kept] == [(1, 6), (7, 8)]
    assert result.rejected == 1


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_kind_or_role_outside_the_pack_is_dropped(combo: Combo) -> None:
    pack = pack_of(combo)
    out = {"entries": [entry(pack, "p1w1", "p1w2", kind="nope"), entry(pack, "p1w3", "p1w4", specialty="nope")]}
    result = extract.sort_entries(out, POSITIONS, pack)
    assert result.kept == [] and result.rejected == 2


def test_no_answer_means_no_entries() -> None:
    pack = pack_of(BOTH[0])
    assert extract.sort_entries(None, POSITIONS, pack).kept == []
    assert extract.sort_entries({}, POSITIONS, pack).rejected == 0


def test_a_rejection_logs_no_word_text(caplog: pytest.LogCaptureFixture) -> None:
    pack = pack_of(BOTH[0])
    with caplog.at_level(logging.DEBUG):
        extract.sort_entries({"entries": [entry(pack, "p1w1", "p9w9")]}, POSITIONS, pack)
    assert not any(text in caplog.text for _, text in WORDS)


# --- the date of an entry (2b): known birth dates are never used; a choice follows an order of rules ---

DOB = dt.date(1986, 5, 12)
DOB_TEXT = "12-May-1986"
SEEN = dt.date(2018, 9, 27)


def laid_out(rows: list[str]) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Words in reading order and the printed line of each (`rows` are lines of text)."""
    ordered: list[tuple[str, str]] = []
    lines: dict[str, int] = {}
    for line_no, row in enumerate(rows, start=1):
        for token in row.split():
            word_id = f"w{len(ordered) + 1}"
            ordered.append((word_id, token))
            lines[word_id] = line_no
    return ordered, lines


def whole(ordered: list[tuple[str, str]], first: int = 1) -> extract.Entry:
    return extract.Entry(
        kind="k",
        first_word_id=ordered[first - 1][0],
        last_word_id=ordered[-1][0],
        first_n=first,
        last_n=len(ordered),
        author_role=None,
        specialty=None,
    )


def first_word_of_line(lines: dict[str, int], line_no: int) -> int:
    return next(n for n, word_id in enumerate(lines, start=1) if lines[word_id] == line_no)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_known_date_of_birth_is_never_the_date_of_an_entry(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out([f"Date of birth {DOB_TEXT}", "Seen 27-Sep-2018 for a routine review"])
    got = extract.entry_date(whole(ordered), ordered, set(), pack, dates_of_birth=[DOB], lines=lines)
    assert got == (SEEN, dates.PRINTED)
    only, _ = laid_out([f"Date of birth {DOB_TEXT} and some more words"])
    assert extract.entry_date(whole(only), only, set(), pack, dates_of_birth=[DOB]) == (None, None)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_same_date_printed_twice_is_one_date(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Seen 27-Sep-2018 by the nurse", "a follow up is booked after 27-Sep-2018 again"])
    assert extract.entry_date(whole(ordered), ordered, set(), pack) == (SEEN, dates.PRINTED)
    assert extract.entry_date(whole(ordered), ordered, set(), pack, lines=lines) == (SEEN, dates.PRINTED)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_with_several_dates_the_one_on_the_first_printed_line_is_used(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Seen 27-Sep-2018 by the nurse", "next review booked for 28-Oct-2018 please"])
    assert extract.entry_date(whole(ordered), ordered, set(), pack, lines=lines) == (SEEN, dates.PRINTED)
    assert extract.entry_date(whole(ordered), ordered, set(), pack) == (None, dates.PLEASE_CHECK), "no lines, no rule"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_two_dates_on_the_first_line_are_left_for_the_reader_to_check(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Collected 27-Sep-2018 Reported 28-Sep-2018 for the lab", "result below the range"])
    assert extract.entry_date(whole(ordered), ordered, set(), pack, lines=lines) == (None, dates.PLEASE_CHECK)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_no_date_gives_no_date_and_a_two_digit_year_alone_is_left_to_check(combo: Combo) -> None:
    pack = pack_of(combo)
    plain, lines = laid_out(["no date is printed anywhere on this entry"])
    assert extract.entry_date(whole(plain), plain, set(), pack, lines=lines) == (None, None)
    short, lines = laid_out(["Seen 12.5.86 by the nurse"])
    assert extract.entry_date(whole(short), short, set(), pack, lines=lines) == (None, dates.PLEASE_CHECK)


def read_in(pack: MergedPack, day_first: dt.date, month_first: dt.date) -> dt.date:
    return day_first if pack.date_order == "DMY" else month_first


ANCHOR = dt.date(2025, 3, 1)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_repair_reads_a_two_digit_year_in_the_century_before_the_bundle_anchor(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Seen 12.5.86 by the nurse"])
    want = read_in(pack, dt.date(1986, 5, 12), dt.date(1986, 12, 5))
    got = extract.repair_entry_date(whole(ordered), ordered, set(), lines, pack, dates_of_birth=[], anchor=ANCHOR)
    assert got == (want, dates.PRINTED_CENTURY)
    again = extract.repair_entry_date(whole(ordered), ordered, set(), lines, pack, dates_of_birth=[want], anchor=ANCHOR)
    assert again == (None, None), "a known date of birth is not used even when it is read from a short year"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_repair_uses_the_dates_rules_one_to_three_before_the_century_rule(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out([f"Date of birth {DOB_TEXT} seen 27-Sep-2018 12.5.86"])
    got = extract.repair_entry_date(whole(ordered), ordered, set(), lines, pack, dates_of_birth=[DOB], anchor=ANCHOR)
    assert got == (SEEN, dates.PRINTED)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_repair_takes_the_date_on_the_line_directly_above(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Seen 27-Sep-2018", "Reviewed by the nurse today", "no change in the plan"])
    entry_ = whole(ordered, first=first_word_of_line(lines, 2))
    got = extract.repair_entry_date(entry_, ordered, set(), lines, pack, dates_of_birth=[], anchor=ANCHOR)
    assert got == (SEEN, dates.LINE_ABOVE)
    far = whole(ordered, first=first_word_of_line(lines, 3))
    assert extract.repair_entry_date(far, ordered, set(), lines, pack, dates_of_birth=[], anchor=ANCHOR) == (None, None)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_repair_never_takes_a_birth_date_or_a_date_below(combo: Combo) -> None:
    pack = pack_of(combo)
    birth, lines = laid_out([f"Date of birth {DOB_TEXT}", "Reviewed by the nurse today"])
    entry_ = whole(birth, first=first_word_of_line(lines, 2))
    got = extract.repair_entry_date(entry_, birth, set(), lines, pack, dates_of_birth=[DOB], anchor=ANCHOR)
    assert got == (None, None)
    below, lines = laid_out(["Reviewed by the nurse today", "Seen 27-Sep-2018"])
    entry_ = extract.Entry("k", "w1", "w4", 1, 4, None, None)
    assert extract.repair_entry_date(entry_, below, set(), lines, pack, dates_of_birth=[], anchor=ANCHOR) == (
        None,
        None,
    )
