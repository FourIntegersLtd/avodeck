"""The curated guideline table: which version was in force on a date (labels, dates and links only,
never guideline text). Rows come from the overlay pack (`pack.guidelines()`)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any


def _day(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10]) if value else None


def in_force(rows: Iterable[Mapping[str, Any]], code: str, on: date) -> Mapping[str, Any] | None:
    for row in rows:
        start, end = _day(row.get("in_force_from")), _day(row.get("in_force_to"))
        if row.get("code") == code and start is not None and start <= on and (end is None or on < end):
            return row
    return None
