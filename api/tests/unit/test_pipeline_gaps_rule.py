"""Slice A increment 6: where a record source goes quiet (architecture 7, `record_gaps`, planted item 2).

`gaps.find(pages)` is pure: each page brings its source, the dates printed on it and its facts'
kinds and dates. A list page is one whose facts are mostly one kind; a source's list pages of one
kind make a series. A series with entries most months that stops at least three months before the
source's last month gives one gap, from the month after its last entry to the source's last month.
Dates printed on most of a source's pages (a birth date, a print date) say nothing about when
entries were made, and one stray date does not move a source's end. Only a series that stops before
the end is a gap: a quiet spell in the middle of a series is not. Kinds and sources come from the
packs, so the same runs under both pack combinations.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace

import pytest

from app.core.packs import MergedPack, default_library
from app.features.pipeline import gaps
from tests.support.packs import BOTH, LAUNCH, Combo

rules = gaps.Rules.from_pack(default_library().combine(*LAUNCH.codes))
HEADER_DATES = (dt.date(1961, 5, 17), dt.date(2025, 3, 1))  # on every page: a birth date and a print date


def month(year: int, n: int) -> dt.date:
    return dt.date(year + (n - 1) // 12, (n - 1) % 12 + 1, 1)


def months(start: dt.date, end: dt.date) -> list[dt.date]:
    out, at = [], start
    while at <= end:
        out.append(at)
        at = month(at.year, at.month + 1)
    return out


class Bundle:
    def __init__(self) -> None:
        self.pages: list[gaps.Page] = []

    def page(
        self, source: str, kind_dates: list[tuple[str, dt.date | None]], printed: list[dt.date], header: bool = True
    ) -> gaps.Page:
        page = gaps.Page(
            page_id=uuid.uuid4(),
            page_no=len(self.pages) + 1,
            source_type=source,
            printed=tuple([*printed, *(HEADER_DATES if header else ())]),
            facts=tuple(kind_dates),
        )
        self.pages.append(page)
        return page

    def series(self, source: str, kind: str, dates: list[dt.date], per_page: int = 12) -> list[gaps.Page]:
        """List pages: `per_page` entries of `kind` each, dated only by the dates printed on the page."""
        return [
            self.page(source, [(kind, None)] * len(chunk), list(chunk))
            for chunk in (dates[i : i + per_page] for i in range(0, len(dates), per_page))
        ]

    def letters(self, source: str, kind: str, dates: list[dt.date]) -> None:
        """One page per date with one dated fact; each date on two pages, so it marks the source's end."""
        for when in dates:
            for _ in range(2):
                self.page(source, [(kind, when)], [when])


def keys(combo: Combo) -> tuple[MergedPack, str, str, str]:
    pack = default_library().combine(*combo.codes)
    kinds = [k["key"] for k in pack.fact_kinds()]
    return pack, pack.source_types()[0]["key"], kinds[0], kinds[1]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_series_that_stops_before_its_source_ends_is_a_gap(combo: Combo) -> None:
    pack, source, listed, other = keys(combo)
    rules = gaps.Rules.from_pack(pack)
    b = Bundle()
    pages = b.series(source, listed, months(dt.date(2021, 1, 1), dt.date(2024, 1, 1)))
    b.letters(source, other, months(dt.date(2023, 6, 1), dt.date(2024, 12, 1)))
    found = gaps.find(b.pages, rules)
    assert found == [
        gaps.Gap(
            source_type=source,
            fact_kind=listed,
            gap_start=dt.date(2024, 2, 1),
            gap_end=dt.date(2024, 12, 31),
            last_page_id=pages[-1].page_id,
        )
    ]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_series_that_runs_to_the_end_is_not_a_gap(combo: Combo) -> None:
    pack, source, listed, other = keys(combo)
    rules = gaps.Rules.from_pack(pack)
    b = Bundle()
    b.series(source, listed, months(dt.date(2021, 1, 1), dt.date(2024, 11, 1)))
    b.letters(source, other, months(dt.date(2023, 6, 1), dt.date(2024, 12, 1)))
    assert gaps.find(b.pages, rules) == []


def test_dates_on_most_pages_of_a_source_do_not_move_its_end() -> None:
    _, source, listed, other = keys(BOTH[0])
    b = Bundle()
    b.series(source, listed, months(dt.date(2021, 1, 1), dt.date(2024, 1, 1)))
    b.letters(source, other, months(dt.date(2023, 6, 1), dt.date(2024, 12, 1)))
    (gap,) = gaps.find(b.pages, rules)
    assert gap.gap_end == dt.date(2024, 12, 31), "a date printed on every page was taken as an entry"


def test_one_stray_date_does_not_move_a_sources_end() -> None:
    _, source, listed, other = keys(BOTH[0])
    b = Bundle()
    b.series(source, listed, months(dt.date(2021, 1, 1), dt.date(2024, 1, 1)))
    b.letters(source, other, months(dt.date(2023, 6, 1), dt.date(2024, 12, 1)))
    b.page(source, [(other, dt.date(2025, 9, 14))], [dt.date(2025, 9, 14)])
    (gap,) = gaps.find(b.pages, rules)
    assert gap.gap_end == dt.date(2024, 12, 31)


def test_a_source_ends_on_its_own_pages_not_another_sources() -> None:
    pack, _, listed, other = keys(BOTH[0])
    first, second = (s["key"] for s in pack.source_types()[:2])
    b = Bundle()
    b.series(first, listed, months(dt.date(2021, 1, 1), dt.date(2024, 1, 1)))
    b.letters(second, other, months(dt.date(2023, 6, 1), dt.date(2024, 12, 1)))
    assert gaps.find(b.pages, rules) == []


def test_pages_of_mixed_kinds_make_no_series() -> None:
    _, source, listed, other = keys(BOTH[0])
    b = Bundle()
    dates = months(dt.date(2021, 1, 1), dt.date(2024, 1, 1))
    for i in range(0, len(dates), 4):
        chunk = dates[i : i + 4]
        b.page(source, [(listed, None), (listed, None), (other, None), (other, None)][: len(chunk)], chunk)  # type: ignore[arg-type]
    b.letters(source, other, months(dt.date(2023, 6, 1), dt.date(2024, 12, 1)))
    assert all(g.fact_kind != listed for g in gaps.find(b.pages, rules))


def test_a_quiet_spell_in_the_middle_of_a_series_is_not_a_gap() -> None:
    _, source, listed, other = keys(BOTH[0])
    b = Bundle()
    quiet = [
        m
        for m in months(dt.date(2019, 1, 1), dt.date(2024, 12, 1))
        if not dt.date(2021, 3, 1) <= m <= dt.date(2021, 12, 1)
    ]
    b.series(source, listed, quiet)
    b.letters(source, other, months(dt.date(2024, 6, 1), dt.date(2024, 12, 1)))
    assert gaps.find(b.pages, rules) == []


def test_a_series_with_few_entries_is_not_regular_enough_to_go_quiet() -> None:
    _, source, listed, other = keys(BOTH[0])
    b = Bundle()
    b.series(source, listed, months(dt.date(2023, 5, 1), dt.date(2024, 1, 1)))  # nine months
    b.letters(source, other, months(dt.date(2023, 6, 1), dt.date(2024, 12, 1)))
    assert gaps.find(b.pages, rules) == []


def test_the_last_entry_is_on_the_latest_page_that_holds_it() -> None:
    _, source, listed, other = keys(BOTH[0])
    b = Bundle()
    dates = months(dt.date(2021, 1, 1), dt.date(2024, 1, 1))
    b.series(source, listed, dates)
    again = b.series(source, listed, dates[-12:])  # the last year printed again on a later page
    b.letters(source, other, months(dt.date(2023, 6, 1), dt.date(2024, 12, 1)))
    (gap,) = gaps.find(b.pages, rules)
    assert gap.last_page_id == again[-1].page_id


def test_the_thresholds_come_from_the_rules_not_the_code() -> None:
    """A series that stops two months before the end is a gap only where the rules say two months is enough."""
    _, source, listed, other = keys(LAUNCH)
    b = Bundle()
    b.series(source, listed, months(dt.date(2021, 1, 1), dt.date(2024, 10, 1)))
    b.letters(source, other, months(dt.date(2023, 6, 1), dt.date(2024, 12, 1)))
    assert gaps.find(b.pages, replace(rules, gap_min_months=3)) == []
    assert len(gaps.find(b.pages, replace(rules, gap_min_months=2))) == 1
