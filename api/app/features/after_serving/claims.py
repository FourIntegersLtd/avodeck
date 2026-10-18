"""Rule 2 for the other expert's claims: code checks each claim against the cited page before any model.

A cited page that is not in the bundle is `not_on_page`. A cited value that is a run of the page's words,
after normalising case, spacing and number format, `matches` with those words. Anything else is left for
the model (T-21c), which only sees the claim, the value and that page's words.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.ai import provenance


@dataclass(frozen=True)
class Settled:
    status: str
    word_ids: list[str] = field(default_factory=list)


def code_check(value: str, words: Sequence[tuple[str, str]] | None) -> Settled | None:
    if words is None:
        return Settled("not_on_page", [])
    wanted = provenance.atoms(value or "")
    if not wanted:
        return None
    held: list[tuple[str, int]] = []
    for index, (_, text) in enumerate(words):
        held.extend((atom, index) for atom in provenance.atoms(text))
    size = len(wanted)
    for i in range(len(held) - size + 1):
        if [a for a, _ in held[i : i + size]] == wanted:
            first, last = held[i][1], held[i + size - 1][1]
            return Settled("matches", [words[n][0] for n in range(first, last + 1)])
    return None
