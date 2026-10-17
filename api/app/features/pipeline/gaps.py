"""Where a record source goes quiet (architecture 7, `record_gaps`): pure code, no model, no database.

Each page brings its source, the dates printed on it and its facts' kinds and dates.

- A source's **header dates** are dates printed on at least 3 of its pages and on at least half of
  them (a birth date, a print date): they say nothing about when entries were made.
- A page's **months** are the months of its other printed dates and of its facts' dates.
- A source **ends** in the latest month dated on at least 2 of its pages, so one stray date does not
  move it.
- A **list page** has at least 3 facts, at least 80% of one kind; a source's list pages of one kind
  make a **series**, whose months are its pages' months. The page straight after a list page whose
  facts are all of the list's kind continues it (the short last page of a printed list).
- A series is **regular** with entries in at least 12 months, at least 6 of them in the 12 months up
  to its last entry.
- A regular series whose last entry is at least 3 months before its source ends is a **gap**, from the
  month after its last entry to the end of the source's last month. A quiet spell inside a series is
  not a gap: only a series that stops is.

The thresholds are about record keeping in general, not any country's rules (rule 11).
"""

from __future__ import annotations

import calendar
import datetime as dt
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

HEADER_MIN_PAGES = 3
SOURCE_END_MIN_PAGES = 2
LIST_MIN_FACTS = 3
LIST_SHARE = 0.8
REGULAR_MONTHS = 12
RECENT_MONTHS = 6
GAP_MIN_MONTHS = 3

Month = tuple[int, int]


@dataclass(frozen=True)
class Page:
    page_id: uuid.UUID
    page_no: int
    source_type: str | None
    printed: tuple[dt.date, ...]
    facts: tuple[tuple[str, dt.date | None], ...]


@dataclass(frozen=True)
class Gap:
    source_type: str
    fact_kind: str
    gap_start: dt.date
    gap_end: dt.date
    last_page_id: uuid.UUID | None


def _month(day: dt.date) -> Month:
    return (day.year, day.month)


def _index(month: Month) -> int:
    return month[0] * 12 + month[1] - 1


def _from_index(n: int) -> Month:
    return (n // 12, n % 12 + 1)


def _headers(pages: Sequence[Page]) -> set[dt.date]:
    counts = Counter(day for p in pages for day in set(p.printed))
    return {day for day, n in counts.items() if n >= HEADER_MIN_PAGES and n * 2 >= len(pages)}


def _months(page: Page, headers: set[dt.date]) -> set[Month]:
    days = [d for d in page.printed if d not in headers] + [d for _, d in page.facts if d and d not in headers]
    return {_month(d) for d in days}


def _end(months: Iterable[set[Month]]) -> Month | None:
    counts = Counter(m for page in months for m in page)
    dated = [m for m, n in counts.items() if n >= SOURCE_END_MIN_PAGES]
    return max(dated) if dated else None


def _list_kind(page: Page) -> str | None:
    counts = Counter(kind for kind, _ in page.facts)
    total = sum(counts.values())
    if total < LIST_MIN_FACTS:
        return None
    kind, n = counts.most_common(1)[0]
    return kind if n >= LIST_SHARE * total else None


def _continues(page: Page, series: dict[str, list[Page]]) -> str | None:
    return next((kind for kind, pages in series.items() if pages and pages[-1] is page), None)


def _regular(months: set[Month]) -> bool:
    if len(months) < REGULAR_MONTHS:
        return False
    last = _index(max(months))
    return sum(1 for m in months if 0 <= last - _index(m) < 12) >= RECENT_MONTHS


def find(pages: Sequence[Page]) -> list[Gap]:
    by_source: dict[str, list[Page]] = defaultdict(list)
    for page in pages:
        if page.source_type:
            by_source[page.source_type].append(page)
    found: list[Gap] = []
    for source, own in sorted(by_source.items()):
        headers = _headers(own)
        months = {p.page_id: _months(p, headers) for p in own}
        end = _end(months.values())
        if end is None:
            continue
        series: dict[str, list[Page]] = defaultdict(list)
        previous: Page | None = None
        for page in sorted(own, key=lambda p: p.page_no):
            kind = _list_kind(page)
            if kind is None and previous is not None and previous.page_no == page.page_no - 1:
                kinds = {k for k, _ in page.facts}
                listed_before = _list_kind(previous) or _continues(previous, series)
                kind = listed_before if kinds == {listed_before} else None
            if kind is not None:
                series[kind].append(page)
            previous = page
        for kind, listed in sorted(series.items()):
            covered = set().union(*(months[p.page_id] for p in listed))
            if not covered or not _regular(covered):
                continue
            last = max(covered)
            if _index(end) - _index(last) < GAP_MIN_MONTHS:
                continue
            holder = max((p for p in listed if last in months[p.page_id]), key=lambda p: p.page_no)
            start = _from_index(_index(last) + 1)
            found.append(
                Gap(
                    source_type=source,
                    fact_kind=kind,
                    gap_start=dt.date(start[0], start[1], 1),
                    gap_end=dt.date(end[0], end[1], calendar.monthrange(*end)[1]),
                    last_page_id=holder.page_id,
                )
            )
    return found
