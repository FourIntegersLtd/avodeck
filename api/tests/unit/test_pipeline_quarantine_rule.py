"""Slice A increment 11b: which pages may belong to someone else (F25, S5, architecture 10.3).

Each page's identifiers are found by the pack's recognisers. The case subject's identifier of a kind
is the one the case knows, or else the one on the most pages; a kind with no single leader is not
used. A page is suggested when it carries an identifier that is not the subject's and none that is.
Pages with no identifiers are never suggested. The values are made up.
"""

from __future__ import annotations

from app.features.pipeline import quarantine

SUBJECT = {"ID": {"A1"}, "DOB": {"1969-03-14"}}
OTHER = {"ID": {"B2"}, "DOB": {"1958-11-02"}}


def test_a_page_with_only_another_persons_identifiers_is_suggested() -> None:
    pages = {1: SUBJECT, 2: SUBJECT, 3: SUBJECT, 4: OTHER, 5: {}}
    assert quarantine.suggest(pages) == [4]


def test_a_page_that_also_carries_the_subjects_identifier_is_not_suggested() -> None:
    pages = {1: SUBJECT, 2: SUBJECT, 3: {"ID": {"A1", "B2"}}}
    assert quarantine.suggest(pages) == []


def test_a_kind_with_no_single_leader_is_not_used() -> None:
    pages = {1: {"PLACE": {"X"}}, 2: {"PLACE": {"Y"}}, 3: SUBJECT, 4: SUBJECT}
    assert quarantine.suggest(pages) == []


def test_identifiers_the_case_knows_decide_over_the_majority() -> None:
    pages = {1: OTHER, 2: OTHER, 3: SUBJECT}
    assert quarantine.suggest(pages, known={"ID": {"A1"}}) == [1, 2]
