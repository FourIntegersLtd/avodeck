"""Whether a page has writing but ended up with little or nothing extracted: pure, no database.

`reason_for` is checked only when the page has no reason yet from OCR or the words check
(`no_words`, `invalid_output`, `not_in_words`): those come first and are never overridden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MIN_WORDS = 40
MIN_COVERAGE = 0.5
MIN_ENTRY_COVERAGE = 0.8  # the default share (the cell's `entry_coverage_floor`) of words entries should cover


def _countable(ordered: Sequence[tuple[str, str]], struck: set[str]) -> list[str]:
    return [word_id for word_id, text in ordered if word_id not in struck and any(ch.isalnum() for ch in text)]


def reason_for(
    *,
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    kept: Sequence[Mapping[str, Any]],
    entries: Sequence[Any] | None,
) -> str | None:
    """`no_facts` (enough words, no kept facts) or `low_coverage` (enough words, under half covered)."""
    countable = _countable(ordered, struck)
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


# A reason that records a page's state without asking a person to look: the page is listed, not flagged.
LIGHT_REASONS = frozenset({"few_words"})


def few_words_reason(
    *,
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    kept: Sequence[Mapping[str, Any]],
    entries: Sequence[Any] | None,
) -> str | None:
    """`few_words`: a page with some words but fewer than `MIN_WORDS` (the line below which `reason_for` never
    speaks, since a cover note or a stamp is short by nature) and nothing extracted. It is a lighter code than
    `no_facts`: it does not mark the page as needing a person."""
    count = len(_countable(ordered, struck))
    if 0 < count < MIN_WORDS and not kept and not entries:
        return "few_words"
    return None


def entry_coverage(*, ordered: Sequence[tuple[str, str]], struck: set[str], entries: Sequence[Any]) -> float:
    """The share of the page's countable words that lie inside an entry (1.0 when it has none to cover)."""
    countable = _countable(ordered, struck)
    if not countable:
        return 1.0
    covered: set[str] = set()
    for entry in entries:
        covered.update(word_id for word_id, _ in ordered[entry.first_n - 1 : entry.last_n])
    return sum(1 for word_id in countable if word_id in covered) / len(countable)


def entry_reason(
    *,
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    entries: Sequence[Any] | None,
    floor: float = MIN_ENTRY_COVERAGE,
) -> str | None:
    """`low_entry_coverage` when a page with enough words has entries over less than `floor` of
    them (none at all counts). It only decides that the page is asked again, never that it needs a person.
    No usable answer (`entries` is None) is left to the caller."""
    if entries is None or len(_countable(ordered, struck)) < MIN_WORDS:
        return None
    share = entry_coverage(ordered=ordered, struck=struck, entries=entries)
    return "low_entry_coverage" if share < floor else None
