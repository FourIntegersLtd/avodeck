"""Slice A: entries precision and recall for `make eval` (recorded mode). Counts only (rule 6)."""

from __future__ import annotations

import pytest

from app.evals.entries import EntryScore, score_entries

E = {"page": 1, "kind": "consultation", "first_n": 1, "last_n": 5}


def test_a_perfect_match_scores_one() -> None:
    s = score_entries([E, {**E, "page": 2}], [E, {**E, "page": 2}])
    assert s == EntryScore(expected=2, found=2, matched=2)
    assert s.precision == 1.0 and s.recall == 1.0


def test_a_wrong_kind_or_span_is_a_miss_and_a_false_find() -> None:
    s = score_entries([E], [{**E, "kind": "letter"}, {**E, "last_n": 4}])
    assert (s.matched, s.found, s.expected) == (0, 2, 1)
    assert s.precision == 0.0 and s.recall == 0.0


def test_an_extra_find_lowers_precision_only() -> None:
    s = score_entries([E], [E, {**E, "page": 9}])
    assert s.precision == 0.5 and s.recall == 1.0


def test_empty_sides_do_not_divide_by_zero() -> None:
    s = score_entries([], [])
    assert s.precision == 1.0 and s.recall == 1.0


def test_each_expected_entry_matches_once() -> None:
    s = score_entries([E], [E, E])
    assert s.matched == 1 and s.precision == 0.5


@pytest.mark.parametrize("bad", [{"page": 1}, {**E, "text": "x"}])
def test_malformed_rows_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        score_entries([bad], [])
