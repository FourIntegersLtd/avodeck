"""The citation rule (rule 2): an opinion cites only citable facts, by the words the fact holds.

The opinion text carries markers `[[cite:n]]`; the n-th `Citation` says which fact, on which page,
and the words quoted (`quote_verbatim`). The quote is compared with the fact's value (which slice A already proved
against the page's OCR words) after the same normalising as provenance: case, spacing, number format.
A refusal names the citation or marker by position and a fixed reason; it never repeats the words.

A citation saved before is kept as it was even if its fact has changed since (moved to a clearer copy,
re-read, no longer citable): architecture 10.4 keeps what was cited. `changed_citations` names those, so
the screen shows "changed since cited" and the expert re-cites. `fact_version` is the fact's version when
cited, if the screen sent it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.core.ai.provenance import atoms
from app.core.contracts.facts import Fact
from app.core.errors import ApiError

_MARKER = re.compile(r"\[\[cite:(\d+)\]\]")


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: uuid.UUID
    page_id: uuid.UUID
    quote_verbatim: str = Field(max_length=2000)
    fact_version: int | None = Field(default=None, ge=1, exclude_if=lambda v: v is None)


def markers(body: str) -> list[int]:
    """The citation numbers in the text, in the order they appear."""
    return [int(n) for n in _MARKER.findall(body)]


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


def _within(quote: str, value: str | None) -> bool:
    wanted, held = atoms(quote), atoms(value or "")
    size = len(wanted)
    return size > 0 and any(held[i : i + size] == wanted for i in range(len(held) - size + 1))


def _differs(citation: Citation, fact: Fact | None) -> bool:
    return (
        fact is None
        or fact.page_id != citation.page_id
        or not _within(citation.quote_verbatim, fact.value)
        or (citation.fact_version is not None and citation.fact_version != fact.version)
    )


def changed_citations(citations: Sequence[Citation], citable: Sequence[Fact]) -> list[int]:
    """The positions (from 1) of the citations whose fact has changed since it was cited."""
    facts = {f.id: f for f in citable}
    return [n for n, c in enumerate(citations, start=1) if _differs(c, facts.get(c.fact_id))]


def check_citations(
    body: str, citations: Sequence[Citation], citable: Sequence[Fact], kept: Sequence[Citation] = ()
) -> None:
    """Raise 422 `invalid` unless every citation is a citable fact's own words and markers match one to one.
    A citation in `kept` (saved before, exactly) is not checked against the fact again."""
    facts = {f.id: f for f in citable}
    for position, citation in enumerate(citations, start=1):
        if citation in kept:
            continue
        fact = facts.get(citation.fact_id)
        if fact is None:
            raise _refuse("not_citable", citation=position)
        if fact.page_id != citation.page_id:
            raise _refuse("wrong_page", citation=position)
        if not _within(citation.quote_verbatim, fact.value):
            raise _refuse("quote_not_in_fact", citation=position)
    used = markers(body)
    for n in used:
        if not 1 <= n <= len(citations):
            raise _refuse("unknown_marker", marker=n)
    for position in range(1, len(citations) + 1):
        if position not in used:
            raise _unused(position)
