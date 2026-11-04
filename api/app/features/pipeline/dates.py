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
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

import dateparser

from app.core.ai.pii import DATE_OF_BIRTH
from app.core.packs import MergedPack

PRINTED = "printed"
PRINTED_CENTURY = "printed_century"
LINE_ABOVE = "line_above"
PLEASE_CHECK = "please_check"
_SWAPPED = {"DMY": "MDY", "MDY": "DMY"}  # the other reading of a day-and-month date
_NUMERIC = re.compile(r"^\W*\d{1,2}[/.\-]\d{1,2}[/.\-](\d{2}|\d{4})\W*$")
_YEAR_FIRST = re.compile(r"^\W*\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2}\W*$")
_MONTH_WORD = re.compile(r"^\W*\d{1,2}[/.\-][^\W\d_]{3,}\.?[/.\-]\d{4}\W*$")
_DAY = re.compile(r"^\W*\d{1,2}\W*$")
_WORD = re.compile(r"^\W*[^\W\d_]{3,}\W*$")
_MONTH_YEAR = re.compile(r"^\W*[^\W\d_]{3,}\.?[/.\-]\d{4}\W*$")
_DASH = re.compile(r"^[-\u2013\u2014]$")
_YEAR = re.compile(r"^\W*\d{4}\W*$")
_ISO = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")
_EDGES = re.compile(r"^\W+|\W+$")


@dataclass(frozen=True)
class Printed:
    word_ids: tuple[str, ...]
    value: dt.date  # read in the pack's date order
    other: dt.date | None  # a numeric date read the other way round, if that is a date too
    ambiguous: bool  # a two-digit year: never used as a fact's date
    century: bool = False  # a two-digit year whose century was chosen (`resolve_century`); no longer ambiguous


@lru_cache(maxsize=4096)
def _read(text: str, order: str, language: str) -> dt.date | None:
    got = dateparser.parse(
        text,
        languages=[language],
        settings={"DATE_ORDER": order, "REQUIRE_PARTS": ["day", "month", "year"], "STRICT_PARSING": True},
    )
    return got.date() if got else None


def read_date(text: str, pack: MergedPack) -> dt.date | None:
    """A whole date written in `text`, read in the pack's date order."""
    return _read(text.strip(), pack.date_order, pack.language)


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
        if (
            i + 2 < len(words)
            and _DAY.match(text)
            and _DASH.match(words[i + 1][1])
            and _MONTH_YEAR.match(words[i + 2][1])
        ):  # a day split from its month and year by a dash: `08 - JUN-2022`
            spoken = f"{_EDGES.sub('', text)}-{_EDGES.sub('', words[i + 2][1])}"
            value = _read(spoken, pack.date_order, pack.language)
            if value is not None:
                found.append(Printed(tuple(w for w, _ in words[i : i + 3]), value, None, ambiguous=False))
                i += 3
                continue
        i += 1
    return found


def is_birth(printed: Printed, births: Collection[dt.date]) -> bool:
    """Whether a printed date is a known date of birth. A date read both ways matches on its printed day
    and month (either reading) and, for a two-digit year, the last two digits of the year in any century."""
    if not births:
        return False
    if not printed.ambiguous:
        return printed.value in births or printed.other in births
    readings = [d for d in (printed.value, printed.other) if d is not None]
    return any((d.day, d.month, d.year % 100) == (b.day, b.month, b.year % 100) for d in readings for b in births)


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


def bundle_anchor(found_all: Iterable[Printed], *, today: dt.date | None = None) -> dt.date:
    """The latest date printed in full across the bundle (never after today), else today.

    A two-digit year is read as the latest calendar day on or before this anchor."""
    today = today or dt.date.today()
    readable = [d.value for d in found_all if not d.ambiguous and d.value <= today]
    return max(readable) if readable else today


def line_numbers(boxes: Iterable[tuple[str, Sequence[float]]]) -> dict[str, int]:
    """Which printed line each word is on, from its box (`x1,y1,x2,y2,...`) in reading order.

    A word starts a new line when its middle sits further from the word before it than 0.6 of the
    taller word's height. A word with no readable box gets a line of its own, never a shared one."""
    out: dict[str, int] = {}
    line = 0
    previous: tuple[float, float] | None = None  # middle and height of the word before
    for word_id, box in boxes:
        ys = list(box[1::2])
        height = (max(ys) - min(ys)) if len(ys) >= 2 else 0.0
        if height <= 0:
            line += 2
            out[word_id] = line
            line += 2
            previous = None
            continue
        middle = (max(ys) + min(ys)) / 2
        if previous is not None and abs(middle - previous[0]) > 0.6 * max(height, previous[1]):
            line += 1
        out[word_id] = line
        previous = (middle, height)
    return out


def pick_past_century(day: int, month: int, yy: int, anchor: dt.date) -> dt.date | None:
    """The latest calendar day on or before `anchor` for a two-digit year (19xx and 20xx)."""
    options: list[dt.date] = []
    for base in (1900, 2000):
        try:
            got = dt.date(base + yy, month, day)
        except ValueError:
            continue
        if got <= anchor:
            options.append(got)
    return max(options) if options else None


def century_from_ambiguous_text(text: str, pack: MergedPack, anchor: dt.date | None = None) -> dt.date | None:
    """Turn a printed numeric date with a two-digit year into one calendar day (anchor defaults to today)."""
    anchor = anchor or dt.date.today()
    found = find([("0", text.strip())], pack)
    if len(found) != 1 or not found[0].ambiguous:
        return None
    match = _NUMERIC.match(text.strip())
    if match is None or len(match.group(1)) != 2:
        return None
    yy = int(match.group(1))
    part = found[0].value
    return pick_past_century(part.day, part.month, yy, anchor)


def resolve_century(printed: Printed, anchor: dt.date, earliest_birth: dt.date | None = None) -> Printed:
    """A two-digit year read in the latest century that is not after `anchor` and, when a date of birth is
    known, not before it (`century=True`, no longer ambiguous). When no century fits it stays ambiguous."""
    if not printed.ambiguous:
        return printed
    day, month, yy = printed.value.day, printed.value.month, printed.value.year % 100
    fits = [
        d
        for base in (1900, 2000)
        for d in [_calendar_day(base + yy, month, day)]
        if d is not None and d <= anchor and (earliest_birth is None or d >= earliest_birth)
    ]
    if not fits:
        return printed
    return Printed(printed.word_ids, max(fits), None, ambiguous=False, century=True)


def _calendar_day(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def labelled_births(
    words: Sequence[tuple[str, str]], pack: MergedPack, anchor: dt.date | None = None
) -> tuple[set[str], list[dt.date]]:
    """The words of every date printed directly after a birth label, and those dates (read in the pack's date
    order; a two-digit year in the latest century not after `anchor`). The label words belong to the pack: its
    identifier recogniser says which text is a date of birth, so nothing country-specific is written here."""
    if not words:
        return set(), []
    spans: list[tuple[int, int]] = []
    text = ""
    for _, word in words:
        start = len(text) + (1 if text else 0)
        spans.append((start, start + len(word)))
        text = f"{text} {word}" if text else word
    ids: set[str] = set()
    births: list[dt.date] = []
    for start, end, kind in pack.identifier_recognisers()(text):
        if kind != DATE_OF_BIRTH:
            continue
        ids.update(w for (w, _), (a, b) in zip(words, spans, strict=True) if a < end and b > start)
        printed = find([(str(n), t) for n, t in enumerate(text[start:end].split())], pack)
        read = printed[0] if len(printed) == 1 else None
        if read is None:
            continue
        if read.ambiguous and anchor is not None:
            read = resolve_century(read, anchor)
        births.append(read.value)
    return ids, births
