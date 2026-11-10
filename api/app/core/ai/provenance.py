"""Rule 2: every extracted value must be found in the OCR words it cites (architecture 6.2.3).

Both sides are cut into atoms (runs of letters, numbers) after Unicode normalisation and case
folding; numbers lose thousands separators, leading zeros and trailing decimal zeros, and a bare
decimal gains its leading zero. A sign or comparator (- + < > ≤ ≥, also "−", "<=", ">=") in front
of a number, unless it follows a letter or digit ("1-2", "Co-amoxiclav"), and a "%" after it stay
part of the number, so "<60" is not "60" and "45%" is not "45". The cited words must be one unbroken
run of the page's words (the order of `words` is reading order), and the value's atoms must appear,
in order and whole, among their atoms. So "AMOXICILLIN 500 mg" is in "Amoxicillin 500mg", "1000"
is in "1,000", but "12" is not in "120" and "amoxi" is not in "Amoxicillin". A number written with a
decimal comma or a slash ("12,5", "3/4") is one number: neither part alone is found in it. A hyphen with a
space on both sides ("120 - 80") separates two numbers; it is not a minus sign. A value carrying an invisible
or control character (right-to-left marks, zero-width characters, soft hyphens, tag characters) is refused
whatever it says. Errors carry a reason code only, never text.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

Reason = Literal["not_in_words", "no_words", "unknown_word", "empty_value", "not_contiguous", "hidden_characters"]

_SIGNS = "-+<>≤≥"
_ATOM = re.compile(
    r"(?:(?<![^\W_])(?:[+<>≤≥]\s*|-))?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:\s*%)?|[^\W\d_]+"
)
_JOINERS = ",/"  # between two digit runs with no space ("12,5", "3/4"): one number written in two parts
_SPELLINGS = (("\u2212", "-"), ("<=", "≤"), (">=", "≥"))


class ProvenanceError(ValueError):
    def __init__(self, reason: Reason) -> None:
        super().__init__(f"value refused: {reason}")
        self.reason: Reason = reason


@dataclass(frozen=True)
class Rejected:
    index: int
    reason: Reason


def _number(atom: str) -> str:
    atom = "".join(atom.split())
    sign = atom[0] if atom[0] in _SIGNS else ""
    percent = "%" if atom.endswith("%") else ""
    return sign + _plain(atom[len(sign) : len(atom) - len(percent)]) + percent


def _plain(atom: str) -> str:
    atom = atom.replace(",", "")
    whole, _, fraction = atom.partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


def atoms(text: str) -> list[str]:
    """The text as atoms. A `,` or `/` written straight between two numbers is kept as an atom of its own, so a
    number written with a decimal comma or a slash is not found by one of its parts (see `contains_whole`)."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    for spelling, sign in _SPELLINGS:
        folded = folded.replace(spelling, sign)
    found: list[str] = []
    joined_at = -1
    for match in _ATOM.finditer(folded):
        raw = match.group()
        if raw[0].isalpha():
            found.append(raw)
            continue
        if found and match.start() == joined_at + 1 and folded[joined_at] in _JOINERS and raw[0].isdigit():
            found.append(folded[joined_at])
        found.append(_number(raw))
        joined_at = match.end() if raw[-1].isdigit() else -1
    return found


def contains_whole(haystack: list[str], needle: list[str]) -> bool:
    """`needle` appears whole in `haystack`: not cut out of the middle of a number written in parts."""
    size = len(needle)
    for i in range(len(haystack) - size + 1):
        if haystack[i : i + size] != needle:
            continue
        if i > 0 and haystack[i - 1] in _JOINERS or i + size < len(haystack) and haystack[i + size] in _JOINERS:
            continue
        return True
    return False


def has_hidden_characters(text: str) -> bool:
    """Whether `text` has an invisible or control character (format and control categories; not tab or newline)."""
    return any(
        unicodedata.category(char) == "Cf" or (unicodedata.category(char) == "Cc" and char not in "\t\n\r")
        for char in text
    )


def check(value: str, word_ids: Sequence[str], words: Mapping[str, str]) -> None:
    """Raise `ProvenanceError` unless `value` is found in the words `word_ids` cite."""
    if has_hidden_characters(value):
        raise ProvenanceError("hidden_characters")
    wanted = atoms(value)
    if not wanted:
        raise ProvenanceError("empty_value")
    if not word_ids:
        raise ProvenanceError("no_words")
    if any(w not in words for w in word_ids):
        raise ProvenanceError("unknown_word")
    order = {w: i for i, w in enumerate(words)}
    positions = sorted({order[w] for w in word_ids})
    if positions[-1] - positions[0] != len(positions) - 1:
        raise ProvenanceError("not_contiguous")
    run = list(words)[positions[0] : positions[-1] + 1]
    cited = atoms(" ".join(words[w] for w in run))
    if not contains_whole(cited, wanted):
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
