"""Citations (rule 2): the shape slice E saves with an opinion, and the re-check slice F runs before signing.

An opinion's text carries markers `[[cite:n]]`; the n-th `Citation` names the fact, its page and the
words quoted (`quote_verbatim`), with the fact's version when cited (`fact_version`) if the screen sent it.
Slice E checks citations as they are saved and keeps a saved one as it was even after its fact changes
(architecture 10.4). `citation_problems()` re-checks every citation in the latest opinion on every
question, kept ones included, against `citable_facts()`, so F's final check fails while any cited fact
has changed, been rejected or gone. Problems carry ids and fixed kinds only, never the quoted words.
Runs on the caller's connection, so row-level security applies.
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field

from app.core.ai.provenance import atoms
from app.core.contracts.facts import Fact, citable_facts
from app.core.contracts.reading import PageReadLog

_MARKER = re.compile(r"\[\[cite:(\d+)\]\]")

CitationProblemKind = Literal["not_citable", "changed_since_cited", "missing"]


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: uuid.UUID
    page_id: uuid.UUID
    quote_verbatim: str = Field(max_length=2000)
    fact_version: int | None = Field(default=None, ge=1, exclude_if=lambda v: v is None)


class CitationProblem(BaseModel):
    """One saved citation that may not be signed as it stands. `citation` is its position from 1, which
    is also its marker number `[[cite:n]]` in the opinion text."""

    model_config = ConfigDict(frozen=True)

    question_id: uuid.UUID
    opinion_id: uuid.UUID
    citation: int
    fact_id: uuid.UUID
    kind: CitationProblemKind


def markers(body: str) -> list[int]:
    """The citation numbers in the text, in the order they appear."""
    return [int(n) for n in _MARKER.findall(body)]


def quote_within(quote: str, value: str | None) -> bool:
    """Whether the quote is a run of the value's words, after provenance normalising."""
    wanted, held = atoms(quote), atoms(value or "")
    size = len(wanted)
    return size > 0 and any(held[i : i + size] == wanted for i in range(len(held) - size + 1))


def differs(citation: Citation, fact: Fact | None) -> bool:
    """Whether the cited fact is not citable now or no longer matches what was cited."""
    return (
        fact is None
        or fact.page_id != citation.page_id
        or not quote_within(citation.quote_verbatim, fact.value)
        or (citation.fact_version is not None and citation.fact_version != fact.version)
    )


async def citation_problems(
    conn: AsyncConnection, case_id: uuid.UUID, reads: PageReadLog | None
) -> list[CitationProblem]:
    """Every saved citation that is `missing`, `not_citable` or `changed_since_cited`, in question order
    then citation order. Empty means every citation still stands. Without a read log nothing is citable."""
    citable = {f.id: f for f in await citable_facts(conn, case_id, reads)}
    cur = await conn.execute(
        "select o.question_id, o.id, o.citation_refs from app.opinions o"
        " join app.questions q on q.id = o.question_id where o.case_id = %s order by q.number, q.id",
        (case_id,),
    )
    saved = [(q, o, [Citation.model_validate(c) for c in refs]) for q, o, refs in await cur.fetchall()]
    cited = sorted({c.fact_id for _, _, refs in saved for c in refs} - citable.keys())
    existing: set[uuid.UUID] = set()
    if cited:
        cur = await conn.execute("select id from app.facts where case_id = %s and id = any(%s)", (case_id, cited))
        existing = {r[0] for r in await cur.fetchall()}
    problems: list[CitationProblem] = []
    for question_id, opinion_id, refs in saved:
        for position, citation in enumerate(refs, start=1):
            kind = _kind(citation, citable.get(citation.fact_id), citation.fact_id in existing)
            if kind is not None:
                problems.append(
                    CitationProblem(
                        question_id=question_id,
                        opinion_id=opinion_id,
                        citation=position,
                        fact_id=citation.fact_id,
                        kind=kind,
                    )
                )
    return problems


def _kind(citation: Citation, fact: Fact | None, exists: bool) -> CitationProblemKind | None:
    if fact is None:
        return "not_citable" if exists else "missing"
    return "changed_since_cited" if differs(citation, fact) else None


__all__ = [
    "Citation",
    "CitationProblem",
    "CitationProblemKind",
    "citation_problems",
    "differs",
    "markers",
    "quote_within",
]
