"""Finding words in the bundle (S09, page viewer batch 2). Pure matching, no database.

Matching ignores case, spacing and punctuation: each word is kept as its letters and numbers only.
A search of one word finds every word containing it; several words must stand next to each other,
each inside the matching word on the page. The words themselves never leave this module's callers
except as places on the page, and are never logged (rule 6).
"""

from __future__ import annotations

from collections.abc import Sequence

MIN_CHARACTERS = 2


def norm(text: str) -> str:
    """The word's letters and numbers, lower case ("Daily." -> "daily")."""
    return "".join(c for c in text.casefold() if c.isalnum())


def terms(query: str) -> list[str]:
    """The search's words, normalised, empty ones dropped."""
    return [t for t in (norm(part) for part in query.split()) if t]


def searchable(query: str) -> bool:
    """At least two letters or numbers to look for."""
    return sum(len(t) for t in terms(query)) >= MIN_CHARACTERS


def tokens(words: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """(word id, normalised word) in reading order. A word the expert read as several words (one
    correction) becomes several tokens on the same word id; words with nothing left are dropped."""
    out: list[tuple[str, str]] = []
    for word_id, text in words:
        out.extend((word_id, t) for t in (norm(part) for part in text.split()) if t)
    return out


def find(words: Sequence[tuple[str, str]], wanted: Sequence[str]) -> list[list[str]]:
    """Each hit as the word ids it covers (in order, no repeats), for the page's `words`."""
    if not wanted:
        return []
    page = tokens(words)
    hits: list[list[str]] = []
    n = len(wanted)
    for i in range(len(page) - n + 1):
        if all(wanted[k] in page[i + k][1] for k in range(n)):
            ids = list(dict.fromkeys(page[i + k][0] for k in range(n)))
            hits.append(ids)
    return hits
