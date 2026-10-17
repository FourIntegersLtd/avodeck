"""Dates read by code from a page's printed words (architecture 6.4, failure mode F12).

- `find(words, pack)`: every date printed on the page, read with the pack's date order and
  language: a numeric date (`03/04/2024`), a year-first date, or a date with its month in words
  (`03-Apr-2024`, `3 Apr 2024`). A numeric date with a two-digit year could be read more than one
  way, so it is marked ambiguous.
- `event_date(model_date, found, pack)`: the model only points at the printed date that belongs
  to a fact; the date itself is the printed one, read in the pack's order. A pointer that matches no
  printed date, or only an ambiguous one, gives no date and `please_check`: never guessed.
- `date_field(pack, kind)`: the kind's first date field in the pack, the one the model points with.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

import dateparser  # type: ignore[import-untyped]

from app.core.packs import MergedPack

PRINTED = "printed"
PLEASE_CHECK = "please_check"
_SWAPPED = {"DMY": "MDY", "MDY": "DMY"}  # the other reading of a day-and-month date
_NUMERIC = re.compile(r"^\W*\d{1,2}[/.\-]\d{1,2}[/.\-](\d{2}|\d{4})\W*$")
_YEAR_FIRST = re.compile(r"^\W*\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2}\W*$")
_MONTH_WORD = re.compile(r"^\W*\d{1,2}[/.\-][^\W\d_]{3,}\.?[/.\-]\d{4}\W*$")
_DAY = re.compile(r"^\W*\d{1,2}\W*$")
_WORD = re.compile(r"^\W*[^\W\d_]{3,}\W*$")
_YEAR = re.compile(r"^\W*\d{4}\W*$")
_ISO = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")
_EDGES = re.compile(r"^\W+|\W+$")


@dataclass(frozen=True)
class Printed:
    word_ids: tuple[str, ...]
    value: dt.date  # read in the pack's date order
    other: dt.date | None  # a numeric date read the other way round, if that is a date too
    ambiguous: bool  # a two-digit year: never used as a fact's date


@lru_cache(maxsize=4096)
def _read(text: str, order: str, language: str) -> dt.date | None:
    got = dateparser.parse(
        text,
        languages=[language],
        settings={"DATE_ORDER": order, "REQUIRE_PARTS": ["day", "month", "year"], "STRICT_PARSING": True},
    )
    return got.date() if got else None


def _numeric(word_id: str, text: str, pack: MergedPack) -> Printed | None:
    order, language = pack.date_order, pack.language
    match = _NUMERIC.match(text)
    if match is None:
        return None
    clean = _EDGES.sub("", text)
    value = _read(clean, order, language)
    if value is None:
        return None
    swapped = _SWAPPED.get(order)
    other = _read(clean, swapped, language) if swapped else None
    return Printed((word_id,), value, other if other != value else None, ambiguous=len(match.group(1)) == 2)


def _single(word_id: str, text: str, pack: MergedPack) -> Printed | None:
    if _YEAR_FIRST.match(text) or _MONTH_WORD.match(text):
        value = _read(_EDGES.sub("", text), pack.date_order, pack.language)
        return Printed((word_id,), value, None, ambiguous=False) if value else None
    return _numeric(word_id, text, pack)


def find(words: Sequence[tuple[str, str]], pack: MergedPack) -> list[Printed]:
    """The dates printed in `words` (`(word_id, text)` in reading order)."""
    found: list[Printed] = []
    i = 0
    while i < len(words):
        word_id, text = words[i]
        one = _single(word_id, text, pack)
        if one is not None:
            found.append(one)
            i += 1
            continue
        if i + 2 < len(words) and _DAY.match(text) and _WORD.match(words[i + 1][1]) and _YEAR.match(words[i + 2][1]):
            spoken = " ".join(_EDGES.sub("", t) for _, t in words[i : i + 3])
            value = _read(spoken, pack.date_order, pack.language)
            if value is not None:
                found.append(Printed(tuple(w for w, _ in words[i : i + 3]), value, None, ambiguous=False))
                i += 3
                continue
        i += 1
    return found


def printed_dates(found: Iterable[Printed]) -> list[dt.date]:
    """The page's dates that read only one way, earliest first."""
    return sorted({d.value for d in found if not d.ambiguous})


def _pointer(model_date: str, pack: MergedPack) -> dt.date | None:
    if _ISO.match(model_date):
        try:
            return dt.date.fromisoformat(model_date.strip())
        except ValueError:
            return None
    read = [d for d in find([(str(n), t) for n, t in enumerate(model_date.split())], pack) if not d.ambiguous]
    return read[0].value if len(read) == 1 else None


def event_date(model_date: str | None, found: Sequence[Printed], pack: MergedPack) -> tuple[dt.date | None, str | None]:
    """A fact's date and its basis: the printed date the model points at, read by code."""
    if model_date is None or not str(model_date).strip():
        return None, None
    target = _pointer(str(model_date), pack)
    if target is None:
        return None, PLEASE_CHECK
    hits = [d for d in found if d.value == target] or [d for d in found if d.other == target]
    values = {d.value for d in hits}
    if not hits or any(d.ambiguous for d in hits) or len(values) != 1:
        return None, PLEASE_CHECK
    return values.pop(), PRINTED


def date_field(pack: MergedPack, kind: str) -> str | None:
    for entry in pack.fact_kinds():
        if entry["key"] == kind:
            return next((name for name, t in entry.get("fields", {}).items() if t == "date"), None)
    return None
