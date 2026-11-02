"""Pages that had enough writing to judge, but ended up with zero facts and no flag telling a
person to check them: the pipeline stayed silent instead of asking. `make eval` fails when the
count is above 0 (the "p.52" case). Rows carry page numbers and counts only, never page text
(rule 6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.features.pipeline.coverage import MIN_WORDS


@dataclass(frozen=True)
class SilentEmptyPages:
    pages: tuple[int, ...]

    @property
    def count(self) -> int:
        return len(self.pages)


def find_silent_empty_pages(rows: Sequence[Mapping[str, Any]]) -> SilentEmptyPages:
    """Each row: {"page": int, "countable_words": int, "facts": int, "needs_human": bool}."""
    return SilentEmptyPages(
        pages=tuple(
            sorted(
                row["page"]
                for row in rows
                if row["countable_words"] >= MIN_WORDS and row["facts"] == 0 and not row["needs_human"]
            )
        )
    )
