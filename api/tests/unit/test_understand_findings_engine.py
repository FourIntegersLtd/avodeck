"""Slice D's code-only pieces around findings, guidelines and link candidates (docs/slices/D-understand.md).

- The checks that run are the pack's, switched on or off by the expert (`default_on: false` starts off);
  an expert may change a window, nothing else. Changes affect new runs only.
- Re-running checks keeps the expert's decision where the same check rests on the same facts.
- The guideline in force on a date is the version with `in_force_from` on or before it and
  `in_force_to` after it (none = still in force).
- Link candidates are the facts most like each question, best first, cut to k, then reranked.
"""

from __future__ import annotations

import uuid
from datetime import date

from app.features.understand.checks.engine import Decided, carry_decisions, configured_checks
from app.features.understand.checks.rules import Hit
from app.features.understand.guidelines import in_force
from app.features.understand.links import IdentityReranker, candidates

PACK_CHECKS = [
    {"key": "a", "rule": "no_action_within", "window_days": 14},
    {"key": "b", "rule": "field_missing", "gentle": True},
    {"key": "c", "rule": "not_followed_up", "window_days": 14, "default_on": False},
]


def test_the_pack_s_checks_run_except_those_starting_off() -> None:
    assert [c["key"] for c in configured_checks(PACK_CHECKS, {})] == ["a", "b"]


def test_an_expert_switches_checks_on_and_off_and_may_change_a_window() -> None:
    config = {"a": {"enabled": False}, "c": {"enabled": True, "window_days": 30, "rule": "made_up"}}
    got = configured_checks(PACK_CHECKS, config)
    assert [c["key"] for c in got] == ["b", "c"]
    assert got[1]["window_days"] == 30
    assert got[1]["rule"] == "not_followed_up", "only the window can be changed"


def test_config_for_a_check_the_pack_no_longer_has_is_ignored() -> None:
    assert [c["key"] for c in configured_checks(PACK_CHECKS, {"gone": {"enabled": True}})] == ["a", "b"]


F1, F2, F3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_a_rerun_keeps_decisions_on_the_same_facts_and_drops_the_rest() -> None:
    old = [
        Decided("a", frozenset({F1, F2}), "not_relevant", "explained in letter"),
        Decided("a", frozenset({F3}), "relevant", None),
        Decided("b", frozenset({F1}), "explained_elsewhere", "see page 4"),
    ]
    new = [Hit("a", (F2, F1)), Hit("a", (F3, F1)), Hit("b", (F2,))]
    got = carry_decisions(new, old)
    assert [(g.hit, g.decision, g.reason) for g in got] == [
        (new[0], "not_relevant", "explained in letter"),
        (new[1], None, None),
        (new[2], None, None),
    ]


ROWS = [
    {"code": "G1", "version_label": "2015", "in_force_from": "2015-06-23", "in_force_to": "2021-01-01"},
    {"code": "G1", "version_label": "2021", "in_force_from": "2021-01-01", "in_force_to": None},
    {"code": "G2", "version_label": "2016", "in_force_from": "2016-07-13", "in_force_to": None},
]


def test_the_version_in_force_on_a_date() -> None:
    assert in_force(ROWS, "G1", date(2020, 12, 31))["version_label"] == "2015"
    assert in_force(ROWS, "G1", date(2021, 1, 1))["version_label"] == "2021"
    assert in_force(ROWS, "G1", date(2030, 1, 1))["version_label"] == "2021"


def test_no_version_before_the_first_or_for_an_unknown_code() -> None:
    assert in_force(ROWS, "G1", date(2015, 6, 22)) is None
    assert in_force(ROWS, "G9", date(2020, 1, 1)) is None


def test_candidates_are_the_most_alike_first_cut_to_k() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    facts = [(a, (0.0, 1.0), "a"), (b, (1.0, 0.0), "b"), (c, (0.7, 0.7), "c")]
    assert candidates(((1.0, 0.0), "q"), facts, k=2, reranker=IdentityReranker()) == [b, c]
    assert candidates(((1.0, 0.0), "q"), [], k=2, reranker=IdentityReranker()) == []


def test_the_reranker_sees_the_question_and_texts_and_has_the_last_word() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    seen: list[tuple[str, list[str]]] = []

    class Reverse:
        def rerank(self, query: str, ranked: list[tuple[uuid.UUID, str]]) -> list[uuid.UUID]:
            seen.append((query, [text for _, text in ranked]))
            return [fid for fid, _ in reversed(ranked)]

    facts = [(a, (1.0, 0.0), "first"), (b, (0.5, 0.5), "second")]
    assert candidates(((1.0, 0.0), "the question"), facts, k=2, reranker=Reverse()) == [b, a]
    assert seen == [("the question", ["first", "second"])]
