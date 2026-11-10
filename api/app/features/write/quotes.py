"""Finding a model's quote in the source words, so only words really there are kept (rule 2)."""

import re

from app.core.ai import provenance

_ENDS_SENTENCE = re.compile(r"[.!?][\"')\]]*$")


def locate_span(quote: str, words: list[tuple[str, str]]) -> tuple[int, int] | None:
    """Where in `words` (reference, text) the run whose text is exactly `quote` (case, spacing and number
    format aside) starts and ends (both included), or None."""
    if provenance.has_hidden_characters(quote):
        return None
    target = provenance.atoms(quote)
    each = [provenance.atoms(text) for _, text in words]
    for start in range(len(words)):
        if not target or not each[start] or each[start][0] != target[0]:
            continue
        run: list[str] = []
        for end in range(start, len(words)):
            run += each[end]
            if run == target:
                return start, end
            if run != target[: len(run)]:
                break
    return None


def locate(quote: str, words: list[tuple[str, str]]) -> list[tuple[str, str]] | None:
    """The run of `words` (reference, text) whose text is exactly `quote` (case, spacing and number
    format aside), or None."""
    found = locate_span(quote, words)
    return None if found is None else words[found[0] : found[1] + 1]


def whole_sentences(words: list[tuple[str, str]], start: int, end: int) -> tuple[int, int]:
    """The run `start`..`end` widened to the sentences it touches, by the statement's own punctuation, so a
    claim never drops a word (such as "never") from the sentence it sits in."""
    while start > 0 and not _ENDS_SENTENCE.search(words[start - 1][1]):
        start -= 1
    while end < len(words) - 1 and not _ENDS_SENTENCE.search(words[end][1]):
        end += 1
    return start, end


def same_words(a: str, b: str) -> bool:
    """Whether two quotes are the same words (case, spacing and number format aside)."""
    return provenance.atoms(a) == provenance.atoms(b)
