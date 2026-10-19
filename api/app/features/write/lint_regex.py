"""The pattern check on an opinion (T-15a "regex first", slice E brief): runs on every save.

Sentence by sentence it flags a date or number with no citation marker in the same sentence
(`uncited_fact`, from the first to the last word with a digit) and the case-type pack's loaded phrases
(`loaded_language`, whole words, any case, quoted as written). It only flags; it never offers other words
(rule 1).
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MARKER = re.compile(r"\[\[cite:\d+\]\]")
_WORD = re.compile(r"\S+")
_EDGE = ".,;:!?()[]\"'"
_MONTHS = {m.casefold() for m in (*calendar.month_name, *calendar.month_abbr) if m}


@dataclass(frozen=True)
class Issue:
    sentence_index: int
    type: str
    words: str


def sentences(body: str) -> list[str]:
    """The opinion's sentences, in order; the index of each is what an issue points at."""
    return [s for s in _SENTENCE_END.split(body.strip()) if s.strip()]


def sentence_spans(body: str) -> list[tuple[int, int]]:
    """Where each sentence of `sentences(body)` sits in `body` (start, end), so the screen can mark it."""
    lead = len(body) - len(body.lstrip())
    text = body.strip()
    spans: list[tuple[int, int]] = []
    pos = 0
    for gap in _SENTENCE_END.finditer(text):
        if text[pos : gap.start()].strip():
            spans.append((lead + pos, lead + gap.start()))
        pos = gap.end()
    if text[pos:].strip():
        spans.append((lead + pos, lead + len(text)))
    return spans


@lru_cache(maxsize=32)
def _loaded(phrases: tuple[str, ...]) -> re.Pattern[str] | None:
    parts = [r"\s+".join(map(re.escape, p.split())) for p in sorted(phrases, key=len, reverse=True) if p.split()]
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE) if parts else None


def _uncited(sentence: str) -> str | None:
    if _MARKER.search(sentence):
        return None
    words = list(_WORD.finditer(sentence))
    digits = [i for i, w in enumerate(words) if any(c.isdigit() for c in w.group(0))]
    if not digits:
        return None
    first, last = digits[0], digits[-1]
    if first > 0 and words[first - 1].group(0).strip(_EDGE).casefold() in _MONTHS:
        first -= 1  # "May 15"
    if last + 1 < len(words) and words[last + 1].group(0).strip(_EDGE).casefold() in _MONTHS:
        last += 1  # "3 June"
    return sentence[words[first].start() : words[last].end()].strip(_EDGE) or None


def lint(body: str, loaded: Sequence[str]) -> list[Issue]:
    """Every issue in `body`, sentence by sentence."""
    pattern = _loaded(tuple(loaded))
    issues = []
    for index, sentence in enumerate(sentences(body)):
        words = _uncited(sentence)
        if words:
            issues.append(Issue(index, "uncited_fact", words))
        if pattern is not None:
            issues.extend(Issue(index, "loaded_language", m.group(0)) for m in pattern.finditer(sentence))
    return issues
