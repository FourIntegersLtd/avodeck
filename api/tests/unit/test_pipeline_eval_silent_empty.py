"""Slice A: the "silent empty pages" figure for `make eval` - pages with enough writing to judge
that ended up with zero facts and no flag telling a person to check them. Counts and page numbers
only, never page text (rule 6)."""

from __future__ import annotations

from app.evals.silent_empty import SilentEmptyPages, find_silent_empty_pages

ROW = {"page": 1, "countable_words": 40, "facts": 0, "needs_human": False}


def test_a_page_with_enough_words_no_facts_and_no_flag_is_silent() -> None:
    result = find_silent_empty_pages([ROW])
    assert result == SilentEmptyPages(pages=(1,))
    assert result.count == 1


def test_a_flagged_page_is_not_silent() -> None:
    result = find_silent_empty_pages([{**ROW, "needs_human": True}])
    assert result == SilentEmptyPages(pages=())
    assert result.count == 0


def test_a_page_with_a_fact_is_not_silent() -> None:
    result = find_silent_empty_pages([{**ROW, "facts": 1}])
    assert result.count == 0


def test_a_page_too_short_to_judge_is_not_silent() -> None:
    result = find_silent_empty_pages([{**ROW, "countable_words": 39}])
    assert result.count == 0


def test_only_silent_pages_are_reported_and_in_page_order() -> None:
    rows = [
        {**ROW, "page": 3},
        {**ROW, "page": 1},
        {**ROW, "page": 2, "needs_human": True},
        {**ROW, "page": 5, "facts": 2},
    ]
    result = find_silent_empty_pages(rows)
    assert result.pages == (1, 3)
    assert result.count == 2


def test_no_rows_is_not_a_failure() -> None:
    assert find_silent_empty_pages([]) == SilentEmptyPages(pages=())
