"""Slice A: an entry's date when the case has no recorded date of birth, two-digit years, and cards that hold
one dated line per visit (plan: entry dates, section 2).

- A date printed directly after a birth label never dates an entry, with or without a recorded date of birth.
  The label words come from the pack's identifier recogniser (rule 11), never from this code.
- A two-digit year is read in the pack's date order, in the latest century not after the anchor and not before
  the date of birth when it is known (`printed_century`); when no century fits the entry is left to check.
- An entry holding two or more lines that start with different printed dates is split into one entry per dated
  line, with contiguous word ranges that together cover the original entry.
Runs under the launch and the fixture packs; the words are made up."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.ai.pii import DATE_OF_BIRTH
from app.core.packs import MergedPack
from app.features.pipeline import dates, extract
from tests.support.packs import BOTH, Combo
from tests.unit.test_pipeline_entries import ANCHOR, first_word_of_line, laid_out, pack_of, whole

LABELS = ("DOB", "Date of birth", "Birthdate:")


def written(pack: MergedPack, day: int, month: int, year: int | str, sep: str = "/") -> str:
    """A numeric date written in the pack's date order."""
    first, second = (day, month) if pack.date_order == "DMY" else (month, day)
    return f"{first}{sep}{second}{sep}{year}"


def label_of(pack: MergedPack) -> str:
    """A birth label the pack's own recogniser reads (the words come from the pack, not from this test)."""
    find = pack.identifier_recognisers()
    for label in LABELS:
        text = f"{label} {written(pack, 14, 3, 1969)}"
        if any(kind == DATE_OF_BIRTH for _, _, kind in find(text)):
            return label
    raise AssertionError("the pack has no birth label this test knows")


def birth(pack: MergedPack) -> dt.date:
    return dt.date(1969, 3, 14)


def seen_on(pack: MergedPack) -> dt.date:
    return dt.date(2012, 11, 11)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_date_after_a_birth_label_never_dates_an_entry_even_with_no_recorded_birth_date(combo: Combo) -> None:
    pack = pack_of(combo)
    dob = f"{label_of(pack)} {written(pack, 14, 3, 1969)}"
    only, lines = laid_out([f"{dob} registered patient"])
    assert extract.entry_date(whole(only), only, set(), pack, [], lines, anchor=ANCHOR) == (None, None)
    both, lines = laid_out([dob, "Seen 27-Sep-2018 by the nurse"])
    got = extract.entry_date(whole(both), both, set(), pack, [], lines, anchor=ANCHOR)
    assert got == (dt.date(2018, 9, 27), dates.PRINTED)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_same_date_printed_again_without_a_label_is_still_the_birth_date_on_that_page(combo: Combo) -> None:
    pack = pack_of(combo)
    dob = written(pack, 14, 3, 1969)
    rows, lines = laid_out([f"{label_of(pack)} {dob}", f"Letter dated {dob} about the patient"])
    assert extract.entry_date(whole(rows), rows, set(), pack, [], lines, anchor=ANCHOR) == (None, None)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_an_ecg_line_with_a_labelled_birth_date_is_dated_by_the_time_of_the_trace(combo: Combo) -> None:
    pack = pack_of(combo)
    row = f"ASHWORTH MRS {label_of(pack)} {written(pack, 14, 3, 1969)} 08-JUN-2022 14:51"
    ordered, lines = laid_out([row])
    got = extract.entry_date(whole(ordered), ordered, set(), pack, [], lines, anchor=ANCHOR)
    assert got == (dt.date(2022, 6, 8), dates.PRINTED)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_date_of_consultation_beside_a_birth_date_is_dated_by_the_consultation(combo: Combo) -> None:
    pack = pack_of(combo)
    row = f"Date of consultation {written(pack, 11, 11, 2012)} {label_of(pack)} {written(pack, 14, 3, 1969)}"
    ordered, lines = laid_out([row])
    got = extract.entry_date(whole(ordered), ordered, set(), pack, [], lines, anchor=ANCHOR)
    assert got == (seen_on(pack), dates.PRINTED)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_two_digit_year_is_read_in_the_latest_century_not_after_the_anchor(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out([f"Seen {written(pack, 12, 6, 88, '.')} by the nurse"])
    got = extract.entry_date(whole(ordered), ordered, set(), pack, [], lines, anchor=ANCHOR)
    assert got == (dt.date(1988, 6, 12), dates.PRINTED_CENTURY)
    recent, lines = laid_out([f"Seen {written(pack, 12, 6, 18, '.')} by the nurse"])
    got = extract.entry_date(whole(recent), recent, set(), pack, [], lines, anchor=ANCHOR)
    assert got == (dt.date(2018, 6, 12), dates.PRINTED_CENTURY)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_two_digit_year_that_would_fall_before_the_date_of_birth_is_left_to_check(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out([f"Seen {written(pack, 12, 6, 68, '.')} by the nurse"])  # 1968 is before the birth
    got = extract.entry_date(whole(ordered), ordered, set(), pack, [birth(pack)], lines, anchor=ANCHOR)
    assert got == (None, dates.PLEASE_CHECK), "2068 is after the anchor, 1968 before the birth"
    labelled, lines = laid_out(
        [f"{label_of(pack)} {written(pack, 14, 3, 1969)}", f"Seen {written(pack, 12, 6, 68, '.')}"]
    )
    got = extract.entry_date(whole(labelled), labelled, set(), pack, [], lines, anchor=ANCHOR)
    assert got == (None, dates.PLEASE_CHECK), "a birth date found by its label limits the century in the same way"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_short_year_no_longer_blocks_a_different_full_date(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Seen 27-Sep-2018 by the nurse", f"Card opened {written(pack, 12, 6, 88, '.')} for her"])
    got = extract.entry_date(whole(ordered), ordered, set(), pack, [], lines, anchor=ANCHOR)
    assert got == (dt.date(2018, 9, 27), dates.PRINTED), "the first printed line decides between two dates"
    without = extract.entry_date(whole(ordered), ordered, set(), pack, [], lines)
    assert without == (None, dates.PLEASE_CHECK), "with no anchor a short year is never read"


# ---- one entry per dated line -------------------------------------------------------------------


def card(*rows: str) -> tuple[list[tuple[str, str]], dict[str, int], extract.Entry]:
    ordered, lines = laid_out(list(rows))
    return ordered, lines, whole(ordered)


def split(
    pack: MergedPack, rows: list[str], **kw: object
) -> tuple[list[extract.Entry], list[tuple[str, str]], dict[str, int]]:
    ordered, lines, entry_ = card(*rows)
    return extract.split_at_dated_lines(entry_, ordered, set(), lines, pack, **kw), ordered, lines  # type: ignore[arg-type]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_card_with_three_dated_lines_is_three_entries_that_cover_it(combo: Combo) -> None:
    pack = pack_of(combo)
    rows = [
        "27-Sep-2018 cough",
        "plan rest and fluids",
        "03-Oct-2018 better",
        "21-Nov-2018 discharged home",
    ]
    got, ordered, lines = split(pack, rows, anchor=ANCHOR)
    assert len(got) == 3
    assert [e.kind for e in got] == ["k", "k", "k"]
    assert got[0].first_n == 1 and got[-1].last_n == len(ordered)
    assert all(b.first_n == a.last_n + 1 for a, b in zip(got, got[1:], strict=False)), "contiguous, no gaps"
    assert got[1].first_n == first_word_of_line(lines, 3) and got[2].first_n == first_word_of_line(lines, 4)
    for e in got:
        assert (e.first_word_id, e.last_word_id) == (ordered[e.first_n - 1][0], ordered[e.last_n - 1][0])
    pieces = [extract.entry_date(e, ordered, set(), pack, [], lines, anchor=ANCHOR) for e in got]
    assert pieces == [
        (dt.date(2018, 9, 27), dates.PRINTED),
        (dt.date(2018, 10, 3), dates.PRINTED),
        (dt.date(2018, 11, 21), dates.PRINTED),
    ]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_lines_that_start_with_a_short_year_date_are_split_too(combo: Combo) -> None:
    pack = pack_of(combo)
    rows = [f"{written(pack, 12, 6, 88, '.')} seen", f"{written(pack, 3, 9, 91, '.')} seen", "plan later"]
    got, _, _ = split(pack, rows, anchor=ANCHOR)
    assert len(got) == 2


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_an_entry_is_not_split_without_two_different_dated_lines(combo: Combo) -> None:
    pack = pack_of(combo)
    one, _, _ = split(pack, ["Seen 27-Sep-2018 cough", "plan rest and fluids"], anchor=ANCHOR)
    assert len(one) == 1
    same, _, _ = split(pack, ["27-Sep-2018 Hb 130", "27-Sep-2018 Plt 250", "note"], anchor=ANCHOR)
    assert len(same) == 1, "two lines of one visit stay together"
    inside, _, _ = split(pack, ["Seen 27-Sep-2018 cough, seen again 03-Oct-2018 better"], anchor=ANCHOR)
    assert len(inside) == 1, "a date in the middle of a line does not start a line"
    ordered, _, entry_ = card("Seen 27-Sep-2018 cough", "Seen 03-Oct-2018 better")
    assert extract.split_at_dated_lines(entry_, ordered, set(), None, pack) == [entry_], "no line positions"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_birth_date_at_the_start_of_a_line_does_not_start_an_entry(combo: Combo) -> None:
    pack = pack_of(combo)
    dob = written(pack, 14, 3, 1969)
    got, _, _ = split(pack, [dob, "Seen 27-Sep-2018 cough", "plan rest"], dates_of_birth=[birth(pack)], anchor=ANCHOR)
    assert len(got) == 1


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_an_ecg_date_whose_day_is_split_from_its_month_by_a_dash_is_read(combo: Combo) -> None:
    pack = pack_of(combo)
    row = f"ASHWORTH MRS {label_of(pack)} {written(pack, 14, 3, 1969)} 08 - JUN-2022 14:51"
    ordered, lines = laid_out([row])
    got = extract.entry_date(whole(ordered), ordered, set(), pack, [], lines, anchor=ANCHOR)
    assert got == (dt.date(2022, 6, 8), dates.PRINTED)
