"""The pattern check on an opinion (slice E brief, T-15a "regex first"; architecture 6.3).

Sentence by sentence, it flags a sentence with a date or a number but no citation marker
(`uncited_fact`, quoting from the first to the last word with a digit), and any phrase from the case-type
pack's loaded-language list (`loaded_language`, quoting the words as written, whole words only, any
case). Citation markers (`[[cite:n]]`) are not numbers. It only flags; it never offers other words
(rule 1). It runs on every save, so a long opinion is checked in under 200 ms.
"""

from __future__ import annotations

import time

import pytest
from app.features.write.lint_regex import Issue, lint

from app.core.packs import default_library
from tests.support.packs import BOTH, Combo

LOADED = ["clearly negligent", "obviously"]


def found(body: str, loaded: list[str] = LOADED) -> list[tuple[int, str, str]]:
    return [(i.sentence_index, i.type, i.words) for i in lint(body, loaded)]


def test_a_sentence_with_a_date_and_no_citation_is_flagged() -> None:
    assert found("She was seen on 15 May 2024 and sent home.") == [(0, "uncited_fact", "15 May 2024")]


def test_a_number_with_no_citation_is_flagged() -> None:
    assert found("Her ferritin was 9 ug/L.") == [(0, "uncited_fact", "9")]


def test_a_cited_sentence_is_not_flagged() -> None:
    assert found("She was seen on 15 May 2024 [[cite:1]] and sent home.") == []


def test_a_citation_marker_is_not_a_number() -> None:
    assert found("My view is set out above [[cite:12]].") == []
    assert found("My view is set out above.") == []


def test_loaded_words_from_the_pack_are_flagged_as_written_whole_words_only() -> None:
    assert found("This was Clearly  negligent care.") == [(0, "loaded_language", "Clearly  negligent")]
    assert found("Obviously the plan was wrong.") == [(0, "loaded_language", "Obviously")]
    assert found("The obviousness of it is not the point.") == []


def test_each_issue_points_at_its_sentence() -> None:
    body = "First I read the records. She came back on 3 June. That was obviously late! Done?"
    assert found(body) == [(1, "uncited_fact", "3 June"), (2, "loaded_language", "obviously")]


def test_one_sentence_can_have_both() -> None:
    assert sorted(found("It was obviously 3 weeks.")) == [(0, "loaded_language", "obviously"), (0, "uncited_fact", "3")]


def test_issues_carry_no_other_words() -> None:
    [issue] = lint("Obviously.", LOADED)
    assert isinstance(issue, Issue) and set(vars(issue)) == {"sentence_index", "type", "words"}


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_the_loaded_words_come_from_the_case_type_pack(combo: Combo) -> None:
    loaded = default_library().combine(*combo.codes).loaded_language()
    assert loaded
    assert found(f"That was {loaded[0]}.", loaded) == [(0, "loaded_language", loaded[0])]


def test_a_long_opinion_is_checked_in_under_200_ms() -> None:
    body = " ".join(
        f"On {n % 28 + 1} May 2024 [[cite:{n}]] the plan was reviewed and it was obviously sound." for n in range(2000)
    )
    lint(body, LOADED)  # warm
    started = time.perf_counter()
    issues = lint(body, LOADED)
    assert time.perf_counter() - started < 0.2
    assert len(issues) == 2000
