"""Slice A increment 4: dates are read by code, in the case's date order (architecture 6.4, F12).

The model only points at the date that belongs to a fact; the date itself is read from the printed
words with the pack's date order and language. A date that could be read two ways (a two-digit
year) or that is not printed on the page is marked "Please check" and never guessed. Runs under
both pack combinations; expected readings come from each pack's own date order.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.packs import MergedPack, default_library
from app.features.pipeline import dates
from tests.support.packs import BOTH, FIXTURE, LAUNCH, Combo

APRIL_3 = dt.date(2024, 4, 3)
MARCH_4 = dt.date(2024, 3, 4)
READ_AS = {"DMY": APRIL_3, "MDY": MARCH_4}  # how 03/04/2024 reads under each date order


def pack_of(combo: Combo) -> MergedPack:
    return default_library().combine(*combo.codes)


def words(*texts: str) -> list[tuple[str, str]]:
    return [(f"p1w{n}", text) for n, text in enumerate(texts, start=1)]


def test_the_launch_pack_reads_03_04_2024_as_3_april() -> None:
    found = dates.find(words("Seen", "03/04/2024", "review"), pack_of(LAUNCH))
    assert dates.printed_dates(found) == [APRIL_3]


def test_the_fixture_pack_reads_03_04_2024_as_4_march() -> None:
    found = dates.find(words("Seen", "03/04/2024", "review"), pack_of(FIXTURE))
    assert dates.printed_dates(found) == [MARCH_4]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_numeric_date_follows_the_pack_date_order(combo: Combo) -> None:
    pack = pack_of(combo)
    found = dates.find(words("Seen", "03/04/2024,", "review"), pack)
    assert dates.printed_dates(found) == [READ_AS[pack.date_order]]
    assert [d.word_ids for d in found] == [("p1w2",)]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_date_with_the_month_in_words_reads_the_same_under_every_pack(combo: Combo) -> None:
    pack = pack_of(combo)
    assert dates.printed_dates(dates.find(words("Seen", "03-Apr-2024", "review"), pack)) == [APRIL_3]
    found = dates.find(words("Seen", "3", "Apr", "2024", "review"), pack)
    assert dates.printed_dates(found) == [APRIL_3]
    assert [d.word_ids for d in found] == [("p1w2", "p1w3", "p1w4")]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_two_digit_year_is_flagged_and_never_guessed(combo: Combo) -> None:
    pack = pack_of(combo)
    found = dates.find(words("Seen", "03/04/24", "review"), pack)
    assert [d.ambiguous for d in found] == [True]
    assert dates.printed_dates(found) == []
    assert dates.event_date("2024-04-03", found, pack) == (None, dates.PLEASE_CHECK)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_the_model_points_at_a_printed_date_and_code_reads_it(combo: Combo) -> None:
    pack = pack_of(combo)
    found = dates.find(words("Seen", "03/04/2024", "review"), pack)
    want = (READ_AS[pack.date_order], dates.PRINTED)
    # Whichever way round the model wrote it, the printed words decide the date.
    assert dates.event_date("2024-04-03", found, pack) == want
    assert dates.event_date("2024-03-04", found, pack) == want
    assert dates.event_date("03/04/2024", found, pack) == want


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_date_not_printed_on_the_page_is_flagged(combo: Combo) -> None:
    pack = pack_of(combo)
    found = dates.find(words("Seen", "03/04/2024", "review"), pack)
    assert dates.event_date("2024-05-09", found, pack) == (None, dates.PLEASE_CHECK)
    assert dates.event_date("not a date", found, pack) == (None, dates.PLEASE_CHECK)
    yearless = dates.find(words("Seen", "03/04", "review"), pack)
    assert yearless == []
    assert dates.event_date("2024-04-03", yearless, pack) == (None, dates.PLEASE_CHECK)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_no_date_from_the_model_means_no_date(combo: Combo) -> None:
    pack = pack_of(combo)
    found = dates.find(words("Seen", "03/04/2024", "review"), pack)
    assert dates.event_date(None, found, pack) == (None, None)
    assert dates.event_date("", found, pack) == (None, None)


def test_the_exact_reading_wins_when_both_orders_are_printed() -> None:
    pack = pack_of(LAUNCH)
    found = dates.find(words("03/04/2024", "and", "04/03/2024"), pack)
    assert dates.printed_dates(found) == [MARCH_4, APRIL_3]
    assert dates.event_date("2024-04-03", found, pack) == (APRIL_3, dates.PRINTED)
    assert dates.event_date("2024-03-04", found, pack) == (MARCH_4, dates.PRINTED)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_the_date_field_is_the_kind_first_date_field_from_the_pack(combo: Combo) -> None:
    pack = pack_of(combo)
    for kind in pack.fact_kinds():
        typed = [name for name, t in kind.get("fields", {}).items() if t == "date"]
        assert dates.date_field(pack, kind["key"]) == (typed[0] if typed else None)
    assert dates.date_field(pack, "no_such_kind") is None
