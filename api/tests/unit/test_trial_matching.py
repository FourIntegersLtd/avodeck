"""Matching the expert's pages to the trial bundle (slice I brief, `match_trial_bundle`).

The trial bundle is re-ordered and re-numbered, leaves three cited pages out and has five re-scanned at
lower quality (the synthetic case `court_bundle_v2`). Every page is matched by comparing the pages
themselves (shingled text, then a closer comparison, then runs of neighbouring pages), never by number.
A close match is confirmed; a weaker one is queued for the expert; a page with no match is missing.
"""

from __future__ import annotations

import time

from app.features.trial.matching import AUTO_CONFIRM, MIN_SCORE, OurPage, match_pages
from tests.fixtures.trial.world import PAGE_NOS, REMOVED, RESCANNED, other_words, page_words, rescan


def bundle() -> tuple[list[OurPage], list[str], dict[int, int]]:
    kept = [n for n in PAGE_NOS if n not in REMOVED]
    order = kept[20:] + kept[:10] + kept[10:20]
    theirs = [" ".join(other_words(n)) for n in range(9)]
    where = {}
    for n in order:
        words = page_words(n)
        theirs.append(" ".join(rescan(words) if n in RESCANNED else words))
        where[n] = len(theirs)
    ours = [OurPage(key=n, page_no=n, text=" ".join(page_words(n))) for n in PAGE_NOS]
    return ours, theirs, where


def test_every_page_is_matched_to_its_trial_number_or_listed_as_missing() -> None:
    ours, theirs, where = bundle()
    found = {m.key: m for m in match_pages(ours, theirs)}
    assert set(found) == set(PAGE_NOS)
    for n in PAGE_NOS:
        if n in REMOVED:
            assert found[n].trial_page_no is None and found[n].method == "none", n
        else:
            assert found[n].trial_page_no == where[n], n


def test_clean_pages_are_confirmed_and_rescanned_pages_score_lower_and_wait_for_the_expert() -> None:
    ours, theirs, _ = bundle()
    found = {m.key: m for m in match_pages(ours, theirs)}
    for n in PAGE_NOS:
        if n in REMOVED:
            continue
        if n in RESCANNED:
            assert MIN_SCORE <= found[n].score < AUTO_CONFIRM and not found[n].confirmed, n
        else:
            assert found[n].score >= AUTO_CONFIRM and found[n].confirmed and found[n].method == "text", n


def test_a_page_number_alone_never_makes_a_match() -> None:
    ours = [OurPage(key=1, page_no=1, text=" ".join(page_words(101)))]
    theirs = [" ".join(other_words(n)) for n in range(3)]
    [only] = match_pages(ours, theirs)
    assert only.trial_page_no is None and only.score < MIN_SCORE


def test_each_trial_page_is_used_once() -> None:
    same = " ".join(page_words(101))
    ours = [OurPage(key=1, page_no=1, text=same), OurPage(key=2, page_no=2, text=same)]
    found = match_pages(ours, [same])
    assert sorted(m.trial_page_no or 0 for m in found) == [0, 1]


def test_a_large_bundle_is_matched_quickly() -> None:
    ours, theirs, _ = bundle()
    theirs = [" ".join(other_words(n)) for n in range(100, 2100)] + theirs
    started = time.perf_counter()
    match_pages(ours, theirs)
    assert time.perf_counter() - started < 20
