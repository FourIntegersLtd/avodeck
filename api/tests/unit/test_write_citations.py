"""The citation rule (rule 2, architecture 5 `PUT .../opinion`, slice E brief).

An opinion cites facts with markers `[[cite:n]]` in its text, each one matching the n-th citation
object `{fact_id, page_id, quote}`. A citation is accepted only when the fact is citable (verified or
edited, on a page the named expert has read), the page is the fact's page, and the quote is words
the fact itself holds (compared after normalising case, spacing and number format). Markers and
citation objects match one to one. Anything else is refused with 422 `invalid`, naming the
citation by position and never echoing the words.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.contracts.facts import Fact
from app.core.errors import ApiError
from app.features.write.citations import Citation, check_citations, markers

NOW = datetime(2026, 10, 18, tzinfo=UTC)
CASE = uuid.uuid4()
PAGE = uuid.uuid4()
OTHER_PAGE = uuid.uuid4()


def fact(value: str = "review 3/52 if no better", page: uuid.UUID = PAGE, status: str = "verified") -> Fact:
    return Fact(
        id=uuid.uuid4(),
        case_id=CASE,
        page_id=page,
        kind="zz_consultation",
        value=value,
        word_ids=["w1"],
        status=status,  # type: ignore[arg-type]
        version=2,
        created_at=NOW,
        updated_at=NOW,
    )


def cite(f: Fact, quote: str | None = None, page: uuid.UUID | None = None) -> Citation:
    return Citation(fact_id=f.id, page_id=page or f.page_id, quote=quote if quote is not None else str(f.value))


def refused(body: str, refs: list[Citation], citable: list[Fact]) -> dict[str, Any]:
    with pytest.raises(ApiError) as caught:
        check_citations(body, refs, citable)
    assert caught.value.status == 422
    assert caught.value.code == "invalid"
    return dict(caught.value.extra)


def test_a_quote_from_a_verified_fact_on_its_page_is_accepted() -> None:
    f = fact()
    check_citations("The plan was to review [[cite:1]].", [cite(f)], [f])


def test_the_quote_may_be_part_of_the_fact_and_differ_in_case_spacing_and_number_format() -> None:
    f = fact("Ferritin 9.0 ug/L on 03/02/2024")
    check_citations("Ferritin was low [[cite:1]].", [cite(f, "ferritin  9 UG/L")], [f])


def test_a_fact_that_is_not_citable_is_refused() -> None:
    f = fact()
    details = refused("See [[cite:1]].", [cite(f)], [])
    assert details == {"citation": 1, "reason": "not_citable"}


def test_a_citation_on_the_wrong_page_is_refused() -> None:
    f = fact()
    details = refused("See [[cite:1]].", [cite(f, page=OTHER_PAGE)], [f])
    assert details == {"citation": 1, "reason": "wrong_page"}


@pytest.mark.parametrize("quote", ["review 2/52 if no better", "she was told to come back", ""])
def test_a_quote_not_in_the_facts_words_is_refused(quote: str) -> None:
    f = fact()
    details = refused("See [[cite:1]].", [cite(f, quote)], [f])
    assert details == {"citation": 1, "reason": "quote_not_in_fact"}


def test_the_refusal_never_echoes_the_words() -> None:
    f = fact()
    with pytest.raises(ApiError) as caught:
        check_citations("See [[cite:1]].", [cite(f, "secret words")], [f])
    assert "secret" not in str(caught.value.message) and "secret" not in str(caught.value.extra)


@pytest.mark.parametrize(
    ("body", "count", "reason"),
    [
        ("No markers at all.", 1, "unused_citation"),
        ("Two [[cite:1]] and [[cite:2]].", 1, "unknown_marker"),
        ("Zero [[cite:0]].", 1, "unknown_marker"),
    ],
)
def test_markers_and_citations_match_one_to_one(body: str, count: int, reason: str) -> None:
    facts = [fact() for _ in range(count)]
    details = refused(body, [cite(f) for f in facts], facts)
    assert details["reason"] == reason


def test_a_marker_may_be_used_more_than_once_and_text_without_citations_is_fine() -> None:
    f = fact()
    check_citations("Here [[cite:1]] and again [[cite:1]].", [cite(f)], [f])
    check_citations("My own words with no facts.", [], [])


def test_markers_are_read_in_order() -> None:
    assert markers("a [[cite:2]] b [[cite:1]] c [[cite:2]] [[cite: 3]]") == [2, 1, 2]
