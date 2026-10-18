"""The citation rule (rule 2): an opinion cites only citable facts, by the words the fact holds.

The opinion text carries markers `[[cite:n]]`; the n-th `Citation` says which fact, on which page,
and the words quoted (`quote_verbatim`). The quote is compared with the fact's value (which slice A already proved
against the page's OCR words) after the same normalising as provenance: case, spacing, number format.
A refusal names the citation or marker by position and a fixed reason; it never repeats the words.
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


def markers(body: str) -> list[int]:
    """The citation numbers in the text, in the order they appear."""
    return [int(n) for n in _MARKER.findall(body)]


def _refuse(reason: str, **where: int) -> ApiError:
    return ApiError("invalid", "A citation in your opinion does not match a checked fact.", 422, **where, reason=reason)


def _within(quote: str, value: str | None) -> bool:
    wanted, held = atoms(quote), atoms(value or "")
    size = len(wanted)
    return size > 0 and any(held[i : i + size] == wanted for i in range(len(held) - size + 1))


def check_citations(body: str, citations: Sequence[Citation], citable: Sequence[Fact]) -> None:
    """Raise 422 `invalid` unless every citation is a citable fact's own words and markers match one to one."""
    facts = {f.id: f for f in citable}
    for position, citation in enumerate(citations, start=1):
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
            raise _refuse("unused_citation", citation=position)
