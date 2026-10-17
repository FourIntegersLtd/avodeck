"""Documents a page mentions that the bundle does not hold (T-07c, architecture 6.2 and 7): pure
pieces, no database.

- `task_for(pack)`: the task with its neutral prompt; the answer is a page number or null and a
  confidence, never prose (rule 1).
- `candidates(pages, date, citing_page_no)`: the pages the model is shown, chosen by code: those
  dated nearest the reference's date, else nearest the citing page.
- `request_text(...)`: the reference's own words, its date and the candidate pages, one per line.
- `description(described, words)`: the model's description only if all its words are among the
  reference's words (rule 2), else the reference's words.
- `NEXT`: the missing-item status changes the API makes (docs/spec/state-machines.md); `requested`
  is set only by sending a request (08b).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

from app.core.ai.kit import AiTask
from app.core.ai.provenance import atoms
from app.core.packs import MergedPack
from app.features.pipeline.gaps import Page

TASK_ID = "T-07c"
PROMPT_VERSION = "2026-10-17.1"
MODEL_ROLE = "sonnet"
CANDIDATES = 40

NEXT: dict[str, frozenset[str]] = {
    "open": frozenset({"requested", "not_available"}),
    "requested": frozenset({"received", "not_available"}),
    "not_available": frozenset({"open"}),
    "received": frozenset(),
}


@cache
def _system() -> str:
    return (Path(__file__).parent / "prompts" / "t07c.txt").read_text()


def output_schema(pack: MergedPack) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["reference_id", "matched_page", "confidence"],
        "properties": {
            "reference_id": {"type": "string", "maxLength": 40},
            "matched_page": {"type": ["integer", "null"], "minimum": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def task_for(pack: MergedPack) -> AiTask:
    return AiTask(
        task_id=TASK_ID, prompt_version=PROMPT_VERSION, model=MODEL_ROLE, system=_system(), schema=output_schema(pack)
    )


def _dates(page: Page) -> list[dt.date]:
    return sorted({*page.printed, *(d for _, d in page.facts if d)})


def candidates(pages: Sequence[Page], date: dt.date | None, citing_page_no: int, limit: int = CANDIDATES) -> list[Page]:
    others = [p for p in pages if p.page_no != citing_page_no]

    def distance(page: Page) -> tuple[int, int]:
        if date is not None:
            days = [abs((d - date).days) for d in _dates(page)]
            return (min(days) if days else 10**6, abs(page.page_no - citing_page_no))
        return (abs(page.page_no - citing_page_no), page.page_no)

    return sorted(sorted(others, key=distance)[:limit], key=lambda p: p.page_no)


def request_text(reference_id: str, words: Sequence[str], date: dt.date | None, pages: Sequence[Page]) -> str:
    lines = [
        f"reference_id: {reference_id}",
        f"reference: {' '.join(' '.join(w.split()) for w in words)}",
        f"date: {date.isoformat() if date else 'none'}",
        "pages:",
    ]
    for page in pages:
        kinds = ", ".join(sorted({k for k, _ in page.facts}))
        dates = ", ".join(d.isoformat() for d in _dates(page)[:6])
        lines.append(f"{page.page_no} | {page.source_type or '-'} | {dates or '-'} | {kinds or '-'}")
    return "\n".join(lines)


def description(described: str, words: Sequence[str]) -> str:
    cited = " ".join(" ".join(w.split()) for w in words)
    wanted = atoms(described)
    return described.strip() if wanted and set(wanted) <= set(atoms(cited)) else cited
