"""Whether a page has writing but ended up with little or nothing extracted: pure, no database.

`reason_for` is checked only when the page has no reason yet from OCR or the words check
(`no_words`, `invalid_output`, `not_in_words`): those come first and are never overridden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MIN_WORDS = 40
MIN_COVERAGE = 0.5


def reason_for(
    *,
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    kept: Sequence[Mapping[str, Any]],
    entries: Sequence[Any] | None,
) -> str | None:
    """`no_facts` (enough words, no kept facts) or `low_coverage` (enough words, under half covered)."""
    countable = [word_id for word_id, text in ordered if word_id not in struck and any(ch.isalnum() for ch in text)]
    if len(countable) < MIN_WORDS:
        return None
    if not kept:
        return "no_facts"
    covered: set[str] = set()
    for fact in kept:
        covered.update(fact.get("word_ids") or [])
    for entry in entries or []:
        covered.update(word_id for word_id, _ in ordered[entry.first_n - 1 : entry.last_n])
    share = sum(1 for word_id in countable if word_id in covered) / len(countable)
    return "low_coverage" if share < MIN_COVERAGE else None
