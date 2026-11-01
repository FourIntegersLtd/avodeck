"""Slice A: flagging a page that has writing but little or nothing extracted (planted item 15).

`coverage.reason_for` is a pure function: given the page's words in reading order, which are
crossed out, which facts survived provenance and which entries were kept, it decides whether the
page needs a person because extraction found nothing (`no_facts`) or far too little
(`low_coverage`). It never runs on a page that already has a reason from OCR or the words check
(that priority is enforced by the caller in `service.py`, not here). Words are made up (rule 6).
"""

from __future__ import annotations

from app.features.pipeline import coverage
from app.features.pipeline.repo import NewEntry


def words(n: int, prefix: str = "alpha") -> list[tuple[str, str]]:
    return [(f"w{i}", f"{prefix}{i}") for i in range(n)]


def entry(first_n: int, last_n: int) -> NewEntry:
    return NewEntry(
        kind="consultation",
        first_n=first_n,
        last_n=last_n,
        author_role=None,
        specialty=None,
        event_date=None,
        date_basis=None,
    )


def test_a_short_page_is_never_judged() -> None:
    ordered = words(39)
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[], entries=[]) is None


def test_enough_words_and_no_facts_needs_a_person() -> None:
    ordered = words(40)
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[], entries=[]) == "no_facts"


def test_no_facts_wins_even_when_an_entry_covers_everything() -> None:
    ordered = words(40)
    covering = entry(1, 40)
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[], entries=[covering]) == "no_facts"


def test_low_coverage_when_little_of_the_page_is_covered() -> None:
    ordered = words(100)
    fact = {"word_ids": ["w98", "w99"]}
    partial = entry(1, 30)  # covers w0..w29, 30 words; the fact adds 2 more distinct words: 32/100
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[fact], entries=[partial]) == "low_coverage"


def test_enough_coverage_is_fine() -> None:
    ordered = words(100)
    fact = {"word_ids": ["w98", "w99"]}
    mostly = entry(1, 60)  # covers w0..w59, 60 words; the fact adds 2 more distinct words: 62/100
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[fact], entries=[mostly]) is None


def test_punctuation_only_words_are_never_counted() -> None:
    ordered = [*words(35), ("p0", "-"), ("p1", "."), ("p2", ","), ("p3", ":"), ("p4", ";"), ("p5", "--"), ("p6", "("), ("p7", ")"), ("p8", "..."), ("p9", "/")]
    assert len(ordered) == 45
    # 35 countable words, all covered by one entry: enough coverage, so None (not too short either).
    covering = entry(1, 35)
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[], entries=[covering]) == "no_facts"
    fact = {"word_ids": [w for w, _ in ordered[:35]]}
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[fact], entries=[]) is None


def test_a_fact_citing_an_unknown_word_id_is_ignored_not_a_crash() -> None:
    ordered = words(40)
    fact = {"word_ids": ["not-on-page", *[f"w{i}" for i in range(25)]]}
    # 25 known words covered out of 40 is below MIN_COVERAGE, and the unknown id must not crash it.
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[fact], entries=[]) == "low_coverage"
