"""Finding a model's quote in the source words, so only words really there are kept (rule 2)."""

from __future__ import annotations

from app.core.ai import provenance


def locate(quote: str, words: list[tuple[str, str]]) -> list[tuple[str, str]] | None:
    """The run of `words` (reference, text) whose text is exactly `quote` (case, spacing and number
    format aside), or None."""
    target = provenance.atoms(quote)
    each = [provenance.atoms(text) for _, text in words]
    for start in range(len(words)):
        if not target or not each[start] or each[start][0] != target[0]:
            continue
        run: list[str] = []
        for end in range(start, len(words)):
            run += each[end]
            if run == target:
                return words[start : end + 1]
            if run != target[: len(run)]:
                break
    return None


def same_words(a: str, b: str) -> bool:
    """Whether two quotes are the same words (case, spacing and number format aside)."""
    return provenance.atoms(a) == provenance.atoms(b)
