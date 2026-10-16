"""Rule 2: every extracted value must be found in the OCR words it cites (architecture 6.2.3).

Both sides are cut into atoms (runs of letters, numbers) after Unicode normalisation and case
folding; numbers lose thousands separators, leading zeros and trailing decimal zeros, and a bare
decimal gains its leading zero. The value's atoms must appear, in order and whole, among the cited
words' atoms. So "AMOXICILLIN 500 mg" is in "Amoxicillin 500mg", "1000" is in "1,000", but "12"
is not in "120" and "amoxi" is not in "Amoxicillin". Errors carry a reason code only, never text.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

Reason = Literal["not_in_words", "no_words", "unknown_word", "empty_value"]

_ATOM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+|[^\W\d_]+")


class ProvenanceError(ValueError):
    def __init__(self, reason: Reason) -> None:
        super().__init__(f"value refused: {reason}")
        self.reason: Reason = reason


@dataclass(frozen=True)
class Rejected:
    index: int
    reason: Reason


def _number(atom: str) -> str:
    atom = atom.replace(",", "")
    whole, _, fraction = atom.partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


def atoms(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return [_number(a) if a[0].isdigit() or a[0] == "." else a for a in _ATOM.findall(folded)]


def _contains(haystack: list[str], needle: list[str]) -> bool:
    size = len(needle)
    return any(haystack[i : i + size] == needle for i in range(len(haystack) - size + 1))


def check(value: str, word_ids: Sequence[str], words: Mapping[str, str]) -> None:
    """Raise `ProvenanceError` unless `value` is found in the words `word_ids` cite."""
    wanted = atoms(value)
    if not wanted:
        raise ProvenanceError("empty_value")
    if not word_ids:
        raise ProvenanceError("no_words")
    if any(w not in words for w in word_ids):
        raise ProvenanceError("unknown_word")
    cited = [a for w in word_ids for a in atoms(words[w])]
    if not _contains(cited, wanted):
        raise ProvenanceError("not_in_words")


def supported(value: str, word_ids: Sequence[str], words: Mapping[str, str]) -> bool:
    try:
        check(value, word_ids, words)
    except ProvenanceError:
        return False
    return True


def check_items(
    items: Sequence[Mapping[str, Any]], words: Mapping[str, str]
) -> tuple[list[Mapping[str, Any]], list[Rejected]]:
    """Split extracted items (`value`, `word_ids`) into accepted and rejected (with reasons)."""
    accepted: list[Mapping[str, Any]] = []
    rejected: list[Rejected] = []
    for index, item in enumerate(items):
        try:
            check(str(item.get("value", "")), list(item.get("word_ids", [])), words)
        except ProvenanceError as exc:
            rejected.append(Rejected(index, exc.reason))
        else:
            accepted.append(item)
    return accepted, rejected
