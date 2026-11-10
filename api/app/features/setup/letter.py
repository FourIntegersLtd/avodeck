"""A letter read by OCR, made ready for a model and checked against afterwards (T-06, T-04).

- `Letter.from_ocr()` flattens the pages into reading order and marks where each line starts.
- `Letter.mask()` runs `pii.strip` over the letter's words and works out each word's masked token, so
  the model sees `{word_id}token` with identifiers already replaced (rule 7; `AiKit.run` strips again).
- `Letter.verify()` applies rule 2: the cited words must be one unbroken run whose words are exactly
  the value given, after normalising. The stored wording is the original words of that run, so it is
  always an exact piece of the letter; the model never supplies the words that are kept (rule 1).
- `Letter.whole_sentences()` makes a checked run start where a sentence, numbered paragraph or item starts and
  stop where one ends, by code from the letter's own words and layout: a run cut from the middle of a sentence is
  widened to the whole sentence (never into the next paragraph), a run that spans two numbered paragraphs is
  refused. A fragment is never stored as a question (a cut "do not ..." would reverse the instruction).
- `Letter.para_ref()` reads the paragraph reference from the letter's layout ("4(a)", "5"), in code.

Nothing here logs or returns text in errors (rule 6).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.ai import pii, provenance
from app.core.ai.ocr import OcrResult
from app.core.packs import MergedPack

_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")
_PARAGRAPH = re.compile(r"^(\d{1,3})\.$")
_ITEM = re.compile(r"^\(([a-z]{1,4}|\d{1,2})\)$")
_CLOSERS = "\"'\u2019\u201d)]"
MAX_RUN_WORDS = 400  # the most words one stored run may hold (the schema's cap on cited word ids)


class MaskingFailed(Exception):
    """The stripped letter could not be lined up with its words; the letter is not sent (fail closed)."""


@dataclass(frozen=True)
class Word:
    id: str
    text: str = field(repr=False)
    line_start: bool


@dataclass(frozen=True)
class Found:
    """A checked piece of the letter: the cited word ids and the original words they cover."""

    word_ids: list[str]
    text: str = field(repr=False)
    start: int


class Letter:
    def __init__(self, words: Sequence[Word]) -> None:
        self.words = list(words)
        self.index = {w.id: i for i, w in enumerate(self.words)}
        self.masked: dict[str, str] = {w.id: w.text for w in self.words}

    @classmethod
    def from_ocr(cls, result: OcrResult) -> Letter:
        words: list[Word] = []
        for page in result.pages:
            last_y: float | None = None
            last_height = 0.0
            for w in page.words:
                ys = w.polygon[1::2] or (0.0,)
                top, height = min(ys), max(ys) - min(ys)
                new_line = last_y is None or abs(top - last_y) > max(height, last_height, 1e-6) / 2
                words.append(Word(w.id, w.text, new_line))
                last_y, last_height = top, height
        return cls(words)

    # --- masking ---------------------------------------------------------------------------------------

    def mask(self, *, pack: MergedPack, names: Sequence[str], dates_of_birth: Sequence[date]) -> None:
        plain = " ".join(w.text for w in self.words)
        stripped = pii.strip(plain, pack=pack, names=names, dates_of_birth=dates_of_birth).text
        spans = _spans(plain, stripped)
        cursor = 0
        for w in self.words:
            start, end = cursor, cursor + len(w.text)
            cursor = end + 1
            token: list[str] = []
            pos = start
            for a, b, placeholder in spans:
                if b <= start or a >= end:
                    continue
                token.append(plain[pos : max(a, start)])
                if a >= start:
                    token.append(placeholder)
                pos = min(b, end)
            token.append(plain[pos:end])
            masked = "".join(token)
            self.masked[w.id] = masked if masked else "\u00b7"

    def prompt_text(self) -> str:
        """The letter as the model sees it: one line per OCR line, each word as `{id}token`."""
        lines: list[list[str]] = []
        for w in self.words:
            if w.line_start or not lines:
                lines.append([])
            lines[-1].append(f"{{{w.id}}}{self.masked[w.id]}")
        return "\n".join(" ".join(line) for line in lines)

    # --- checks ----------------------------------------------------------------------------------------

    def verify(self, value: str, word_ids: Sequence[str]) -> Found | provenance.Reason | str:
        """The checked piece, or a reason code. The value must be the whole of the cited run."""
        try:
            provenance.check(value, word_ids, self.masked)
        except provenance.ProvenanceError as exc:
            return exc.reason
        positions = sorted({self.index[w] for w in word_ids})
        run = self.words[positions[0] : positions[-1] + 1]
        if provenance.atoms(value) != provenance.atoms(" ".join(self.masked[w.id] for w in run)):
            return "not_whole_words"
        return Found([w.id for w in run], " ".join(w.text for w in run), positions[0])

    # --- sentence and paragraph boundaries ---------------------------------------------------------------

    def _paragraph_marker(self, i: int) -> bool:
        w = self.words[i]
        return w.line_start and _PARAGRAPH.match(w.text) is not None

    def _marker(self, i: int) -> bool:
        return self._paragraph_marker(i) or _ITEM.match(self.words[i].text) is not None

    def _sentence_end(self, i: int) -> bool:
        """The word closes a sentence: it ends in . ? or !, is not a paragraph number, is not an abbreviation
        written with inner dots, and is not followed by a lower-case word. Doubt widens a run (safe), never trims."""
        if self._paragraph_marker(i):
            return False
        text = self.words[i].text.rstrip(_CLOSERS)
        if not text or text[-1] not in ".?!" or "." in text[:-1]:
            return False
        if text[-1] == "." and len(text) <= 4 and text[0].isupper():
            return False  # a short capitalised token with a full stop ("Dr.", "Mr.") may be an abbreviation: widen
        nxt = self.words[i + 1].text if i + 1 < len(self.words) else ""
        return not (nxt[:1].islower())

    def _starts(self, i: int) -> bool:
        return i == 0 or self._marker(i - 1) or self._sentence_end(i - 1)

    def _ends(self, i: int) -> bool:
        return i == len(self.words) - 1 or self._sentence_end(i) or self._marker(i + 1)

    def whole_sentences(self, found: Found) -> Found | str:
        """The run widened to whole sentences, or a reason code. Never crosses a paragraph number; a run that
        holds one (two paragraphs given as one question) is refused. Leading markers are dropped from the run."""
        positions = sorted(self.index[w] for w in found.word_ids)
        lo, hi = positions[0], positions[-1]
        while lo < hi and self._marker(lo):
            lo += 1
        if any(self._paragraph_marker(k) for k in range(lo + 1, hi + 1)):
            return "spans_paragraphs"
        while not self._starts(lo):
            lo -= 1
        while not self._ends(hi):
            hi += 1
        run = self.words[lo : hi + 1]
        if len(run) > MAX_RUN_WORDS:
            return "too_long"
        return Found([w.id for w in run], " ".join(w.text for w in run), lo)

    def para_ref(self, start: int) -> str | None:
        """ "4(a)" when the run follows an item marker inside paragraph 4; "5" for paragraph 5 itself."""
        item = None
        if start > 0 and (m := _ITEM.match(self.words[start - 1].text)):
            item = m.group(1)
        paragraph = None
        for w in reversed(self.words[:start]):
            if w.line_start and (m := _PARAGRAPH.match(w.text)):
                paragraph = m.group(1)
                break
        if paragraph is None:
            return None if item is None else f"({item})"
        return paragraph if item is None else f"{paragraph}({item})"


def _spans(original: str, stripped: str) -> list[tuple[int, int, str]]:
    placeholders = _PLACEHOLDER.findall(stripped)
    if not placeholders:
        if original != stripped:
            raise MaskingFailed
        return []
    literals = _PLACEHOLDER.split(stripped)
    pattern = re.escape(literals[0]) + "".join(f"(.+?){re.escape(lit)}" for lit in literals[1:])
    match = re.fullmatch(pattern, original, re.S)
    if match is None:
        raise MaskingFailed
    return [(match.start(i + 1), match.end(i + 1), p) for i, p in enumerate(placeholders)]


def checked_items(
    letter: Letter, items: Sequence[Mapping[str, Any]]
) -> tuple[list[tuple[Mapping[str, Any], Found]], list[str]]:
    """Split model items (`text_verbatim`, `word_ids`) into checked ones and rejection reasons."""
    accepted: list[tuple[Mapping[str, Any], Found]] = []
    rejected: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for item in items:
        found = letter.verify(str(item.get("text_verbatim", "")), list(item.get("word_ids", [])))
        if isinstance(found, Found):
            found = letter.whole_sentences(found)
        if not isinstance(found, Found):
            rejected.append(found)
        elif tuple(found.word_ids) in seen:
            rejected.append("duplicate")  # two cuts of one sentence are one question
        else:
            seen.add(tuple(found.word_ids))
            accepted.append((item, found))
    return accepted, rejected


SHORT_TITLE_MAX = 40
_CLAUSE_END = re.compile(r"[,;:?!]|\.(?=\s|$)")


def own_label(text: str, label: object) -> str | None:
    """The model's label when it is a run of the question's own words, side by side and in the question's order
    (same word normalisation as the letter check), it has no hidden character and it fits the limit; else None.
    A steered model cannot put its own words on the question tab, and the same words in another order cannot turn
    the question round ("it was reasonable" for "was it not reasonable"). AUDIT-14 O-1."""
    if not isinstance(label, str) or provenance.has_hidden_characters(label):
        return None
    clean = " ".join(label.split())
    wanted = provenance.atoms(clean)
    if not wanted or len(clean) > SHORT_TITLE_MAX:
        return None
    return label if provenance.contains_whole(provenance.atoms(text), wanted) else None


def short_title(text: str, label: str | None = None) -> str:
    """A label of at most 40 characters for a question, for tabs and lists.

    The model's `short_title` when it is a clean label (one line, 1 to 40 characters, no placeholder from the
    identifier strip), else the first clause of the question's own words, cut at a word. A label only: the
    model's words are never kept as prose and the question's own words are never changed (rule 1).
    """
    if label is not None:
        clean = " ".join(label.split())
        if clean and "\n" not in label and len(clean) <= SHORT_TITLE_MAX and not _PLACEHOLDER.search(clean):
            return clean
    words = " ".join(text.split())
    end = _CLAUSE_END.search(words)
    clause = (words[: end.start()] if end else words).strip() or words
    if len(clause) <= SHORT_TITLE_MAX:
        return clause
    cut = clause[: SHORT_TITLE_MAX + 1]
    return (cut[: cut.rfind(" ")] if " " in cut else clause[:SHORT_TITLE_MAX]).rstrip(" ,;:.")
