"""Precision and recall of page entries (ADR 0028) against an expected list, for `make eval`.

An entry matches when page, kind and both ends agree; each expected entry is matched once. Rows carry page
numbers, keys and word numbers only, never text (rule 6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

KEYS = frozenset({"page", "kind", "first_n", "last_n"})


@dataclass(frozen=True)
class EntryScore:
    expected: int
    found: int
    matched: int

    @property
    def precision(self) -> float:
        return self.matched / self.found if self.found else 1.0

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0


def _key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    if set(row) != KEYS:
        raise ValueError("an entry row has page, kind, first_n and last_n and nothing else")
    return (row["page"], row["kind"], row["first_n"], row["last_n"])


def score_entries(expected: Sequence[Mapping[str, Any]], found: Sequence[Mapping[str, Any]]) -> EntryScore:
    wanted = [_key(r) for r in expected]
    got = [_key(r) for r in found]
    matched = 0
    for key in got:
        if key in wanted:
            wanted.remove(key)
            matched += 1
    return EntryScore(expected=len(expected), found=len(got), matched=matched)
