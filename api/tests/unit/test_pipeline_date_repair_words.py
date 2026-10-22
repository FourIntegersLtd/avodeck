"""Slice A: repair_fact_dates uses printed words only (F12)."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.packs import MergedPack, default_library
from app.features.pipeline import date_repair, dates
from tests.support.packs import LAUNCH, Combo

APRIL_3 = dt.date(2024, 4, 3)
MARCH_4 = dt.date(2024, 3, 4)
READ_AS = {"DMY": APRIL_3, "MDY": MARCH_4}


def pack_of(combo: Combo) -> MergedPack:
    return default_library().combine(*combo.codes)


def words(*pairs: tuple[str, str]) -> list[tuple[str, str]]:
    return list(pairs)


def test_pointer_on_page_sets_event_date() -> None:
    pack = pack_of(LAUNCH)
    ordered = words(("w1", "Seen"), ("w2", "03/04/2024"), ("w3", "review"))
    kind = pack.fact_kinds()[0]["key"]
    field = dates.date_field(pack, kind)
    assert field is not None
    when, basis = date_repair.resolve_fact_event_date(
        kind, {field: "03/04/2024"}, ["w2", "w3"], ordered, set(), pack
    )
    assert when == READ_AS[pack.date_order]
    assert basis == dates.PRINTED


@pytest.mark.parametrize("combo", [LAUNCH], ids=lambda c: c.codes[0])
def test_ambiguous_two_digit_year_stays_undated(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered = words(("w1", "03/04/24"))
    kind = pack.fact_kinds()[0]["key"]
    field = dates.date_field(pack, kind)
    assert field is not None
    when, basis = date_repair.resolve_fact_event_date(kind, {field: "2024-04-03"}, ["w1"], ordered, set(), pack)
    assert when is None
    assert basis is None


def test_single_date_in_cited_words_without_pointer() -> None:
    pack = default_library().combine(*LAUNCH.codes)
    ordered = words(("w1", "11.8.1995"), ("w2", "mole"))
    kind = pack.fact_kinds()[0]["key"]
    when, basis = date_repair.resolve_fact_event_date(kind, {}, ["w1", "w2"], ordered, set(), pack)
    assert when == dt.date(1995, 8, 11)
    assert basis == dates.PRINTED


def test_two_digit_year_in_cited_words_stays_undated() -> None:
    pack = default_library().combine(*LAUNCH.codes)
    ordered = words(("w1", "11.8.95"), ("w2", "mole"))
    kind = pack.fact_kinds()[0]["key"]
    when, basis = date_repair.resolve_fact_event_date(kind, {}, ["w1", "w2"], ordered, set(), pack)
    assert when is None
    assert basis is None


def test_two_digit_year_resolves_when_century_allowed() -> None:
    pack = default_library().combine(*LAUNCH.codes)
    ordered = words(("w1", "19.5.93"), ("w2", "BP"))
    kind = pack.fact_kinds()[0]["key"]
    field = dates.date_field(pack, kind)
    anchor = dt.date(2025, 10, 21)
    when, basis = date_repair.resolve_fact_event_date(
        kind,
        {field: "19.5.93"} if field else {},
        ["w1", "w2"],
        ordered,
        set(),
        pack,
        allow_century=True,
        anchor=anchor,
    )
    assert when == dt.date(1993, 5, 19)
    assert basis == dates.PRINTED_CENTURY
