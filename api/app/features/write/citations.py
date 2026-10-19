"""The citation rule (rule 2): an opinion cites only citable facts, by the words the fact holds.

The opinion text carries markers `[[cite:n]]`; the n-th `Citation` says which fact, on which page,
and the words quoted (`quote_verbatim`). The quote is compared with the fact's value (which slice A already proved
against the page's OCR words) after the same normalising as provenance: case, spacing, number format.
A refusal names the citation or marker by position and a fixed reason; it never repeats the words.

A citation saved before is kept as it was even if its fact has changed since (moved to a clearer copy,
re-read, no longer citable): architecture 10.4 keeps what was cited. `changed_citations` names those, so
the screen shows "changed since cited" and the expert re-cites. `fact_version` is the fact's version when
cited; the server stamps it when the screen does not send it.

The citation shape and the comparing helpers live in `app.core.contracts.citations`, shared with slice F,
which re-checks every saved citation (kept ones included) with `citation_problems()` before signing.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.contracts.citations import Citation, differs, markers, quote_within
from app.core.contracts.facts import Fact
from app.core.errors import ApiError

__all__ = ["Citation", "changed_citations", "check_citations", "markers", "same_citation", "stamp_versions"]


def _refuse(reason: str, **where: int) -> ApiError:
    return ApiError("invalid", "A citation in your opinion does not match a checked fact.", 422, **where, reason=reason)


def _unused(position: int) -> ApiError:
    return ApiError(
        "invalid",
        f"Citation [{position}] is no longer in your text. Add it back or remove the citation.",
        422,
        citation=position,
        marker=position,
        reason="unused_citation",
    )


def same_citation(sent: Citation, stored: Citation) -> bool:
    """A resent citation is the stored one; a missing `fact_version` on the sent copy matches the stamped one."""
    if sent.fact_version is None:
        return sent == stored.model_copy(update={"fact_version": None})
    return sent == stored


def stamp_versions(citations: Sequence[Citation], citable: Sequence[Fact], kept: Sequence[Citation]) -> list[Citation]:
    """Each citation without a `fact_version` gets the one it was stamped with before, else its fact's current one."""
    facts = {f.id: f for f in citable}
    stamped: list[Citation] = []
    for citation in citations:
        if citation.fact_version is None:
            old = next((k for k in kept if same_citation(citation, k)), None)
            fact = facts.get(citation.fact_id)
            version = old.fact_version if old is not None else (fact.version if fact is not None else None)
            citation = citation.model_copy(update={"fact_version": version})
        stamped.append(citation)
    return stamped


def changed_citations(citations: Sequence[Citation], citable: Sequence[Fact]) -> list[int]:
    """The positions (from 1) of the citations whose fact has changed since it was cited."""
    facts = {f.id: f for f in citable}
    return [n for n, c in enumerate(citations, start=1) if differs(c, facts.get(c.fact_id))]


def check_citations(
    body: str, citations: Sequence[Citation], citable: Sequence[Fact], kept: Sequence[Citation] = ()
) -> None:
    """Raise 422 `invalid` unless every citation is a citable fact's own words and markers match one to one.
    A citation in `kept` (saved before, exactly) is not checked against the fact again."""
    facts = {f.id: f for f in citable}
    for position, citation in enumerate(citations, start=1):
        if any(same_citation(citation, k) for k in kept):
            continue
        fact = facts.get(citation.fact_id)
        if fact is None:
            raise _refuse("not_citable", citation=position)
        if fact.page_id != citation.page_id:
            raise _refuse("wrong_page", citation=position)
        if not quote_within(citation.quote_verbatim, fact.value):
            raise _refuse("quote_not_in_fact", citation=position)
    used = markers(body)
    for n in used:
        if not 1 <= n <= len(citations):
            raise _refuse("unknown_marker", marker=n)
    for position in range(1, len(citations) + 1):
        if position not in used:
            raise _unused(position)
