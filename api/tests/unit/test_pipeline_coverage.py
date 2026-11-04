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
    punctuation = [
        ("p0", "-"),
        ("p1", "."),
        ("p2", ","),
        ("p3", ":"),
        ("p4", ";"),
        ("p5", "--"),
        ("p6", "("),
        ("p7", ")"),
        ("p8", "..."),
        ("p9", "/"),
    ]
    ordered = [*words(35), *punctuation]
    assert len(ordered) == 45
    # Only 35 real (countable) words: too short to judge, however the punctuation words are covered.
    fact = {"word_ids": [w for w, _ in ordered]}  # covers everything, punctuation included
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[fact], entries=[]) is None
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[], entries=[]) is None


def test_a_fact_citing_an_unknown_word_id_is_ignored_not_a_crash() -> None:
    ordered = words(40)
    fact = {"word_ids": ["not-on-page", *[f"w{i}" for i in range(15)]]}
    # 15 known words covered out of 40 is below MIN_COVERAGE, and the unknown id must not crash it.
    assert coverage.reason_for(ordered=ordered, struck=set(), kept=[fact], entries=[]) == "low_coverage"


# --- how much of the page the entries cover (2a, 2e) ---


def test_entry_coverage_is_the_share_of_countable_words_inside_entries() -> None:
    ordered = words(100)
    assert coverage.entry_coverage(ordered=ordered, struck=set(), entries=[entry(1, 60)]) == 0.6
    assert coverage.entry_coverage(ordered=ordered, struck=set(), entries=[entry(1, 30), entry(31, 50)]) == 0.5
    assert coverage.entry_coverage(ordered=ordered, struck=set(), entries=[]) == 0.0


def test_entry_coverage_leaves_out_crossed_out_words_and_punctuation() -> None:
    ordered = [*words(8), ("p0", "-"), ("p1", "."), ("s0", "crossed"), ("s1", "words")]
    covered = coverage.entry_coverage(ordered=ordered, struck={"s0", "s1"}, entries=[entry(1, 4)])
    assert covered == 0.5, "4 of the 8 countable words"
    assert coverage.entry_coverage(ordered=[("p0", "-")], struck=set(), entries=[]) == 1.0, "nothing to cover"


def test_a_page_with_enough_words_and_few_inside_entries_is_asked_again() -> None:
    assert coverage.MIN_ENTRY_COVERAGE == 0.8
    ordered = words(100)
    assert coverage.entry_reason(ordered=ordered, struck=set(), entries=[entry(1, 79)]) == "low_entry_coverage"
    assert coverage.entry_reason(ordered=ordered, struck=set(), entries=[entry(1, 80)]) is None
    assert coverage.entry_reason(ordered=ordered, struck=set(), entries=[entry(1, 100)]) is None


def test_a_page_with_enough_words_and_no_entries_is_asked_again() -> None:
    assert coverage.entry_reason(ordered=words(40), struck=set(), entries=[]) == "low_entry_coverage"


def test_a_short_page_or_an_unusable_answer_is_never_asked_again_for_entries() -> None:
    assert coverage.entry_reason(ordered=words(39), struck=set(), entries=[]) is None
    assert coverage.entry_reason(ordered=words(100), struck=set(), entries=None) is None
