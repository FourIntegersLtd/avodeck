"""A document read by OCR, made ready for a model and checked against afterwards (T-20a, T-21b).

A copy of slice C's letter reader (features do not import each other): the other side's questions and
their expert's report are read the same way.

- `Letter.from_ocr()` flattens the pages into reading order and marks where each line starts.
- `Letter.mask()` runs `pii.strip` over the letter's words and works out each word's masked token, so
  the model sees `{word_id}token` with identifiers already replaced (rule 7; `AiKit.run` strips again).
- `Letter.verify()` applies rule 2: the cited words must be one unbroken run whose words are exactly
  the value given, after normalising. The stored wording is the original words of that run, so it is
  always an exact piece of the letter; the model never supplies the words that are kept (rule 1).
- `Letter.para_ref()` reads the paragraph reference from the letter's layout ("4(a)", "5"), in code.

Nothing here logs or returns text in errors (rule 6).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.ai import pii, provenance
from app.core.ai.ocr import OcrResult
from app.core.packs import MergedPack

_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")
_PARAGRAPH = re.compile(r"^(\d{1,3})\.$")
_ITEM = re.compile(r"^\(([a-z]{1,4}|\d{1,2})\)$")
_NUMBER = re.compile(r"^(\d{1,6})$")
_EDGE = ".,;:()[]\"'"
_FURNITURE_WORDS = 6  # a page-number line is short ("Page 3 of 20")


def _pages(sentence: Sequence[str], markers: Sequence[str]) -> list[str]:
    """The page numbers a sentence cites: a marker word followed by a number ("page 12", "p.12", "p 12")."""
    words = [w.strip(_EDGE) for w in sentence]
    found: list[str] = []
    for i, token in enumerate(words):
        low = token.casefold()
        number = None
        for marker in markers:
            if low == marker and i + 1 < len(words) and (m := _NUMBER.match(words[i + 1])):
                number = m.group(1)
            elif low.startswith(marker) and (m := _NUMBER.match(low[len(marker) :].lstrip("."))):
                number = m.group(1)
            if number:
                break
        if number:
            found.append(str(int(number)))
    return found


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
            self.masked[w.id] = "".join(token)

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

    def _furniture(self, markers: Sequence[str]) -> set[str]:
        """Ids of the words on page-furniture lines ("Page 3 of 20"): a short line that only numbers a page."""
        lines: list[list[Word]] = []
        for w in self.words:
            if w.line_start or not lines:
                lines.append([])
            lines[-1].append(w)
        found: set[str] = set()
        for line in lines:
            texts = [w.text for w in line]
            plain = [t.strip(_EDGE) for t in texts]
            if (
                len(line) <= _FURNITURE_WORDS
                and not texts[-1].endswith((".", "?", "!"))
                and _pages(texts, markers)
                and all(t.isdigit() or t.casefold() in markers or len(t) <= 3 for t in plain)
            ):
                found.update(w.id for w in line)
        return found

    def _sentences(self, words: Sequence[Word], skip: Collection[str] = ()) -> Iterator[list[str]]:
        current: list[str] = []
        for w in words:
            if w.id in skip:
                continue  # page furniture is neither part of a sentence nor the end of one
            if w.line_start and _PARAGRAPH.match(w.text):
                continue  # a paragraph number is neither part of a sentence nor the end of one
            current.append(w.text)
            if w.text.endswith((".", "?", "!")):
                yield current
                current = []
        if current:
            yield current

    def page_sentences(self, markers: Sequence[str]) -> int:
        """How many sentences of the letter name a page, counted from its own words (never from a model)."""
        skip = self._furniture(markers)
        return sum(1 for s in self._sentences(self.words, skip) if _pages(s, markers))

    def cited_page(self, found: Found, markers: Sequence[str]) -> str | None:
        """The one page the claim's first page-naming sentence cites, read from its own words; none if unclear."""
        first = self.index[found.word_ids[0]]
        skip = self._furniture(markers)
        for sentence in self._sentences(self.words[first : first + len(found.word_ids)], skip):
            pages = set(_pages(sentence, markers))
            if pages:
                return next(iter(pages)) if len(pages) == 1 else None
        return None

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
    for item in items:
        found = letter.verify(str(item.get("text_verbatim", "")), list(item.get("word_ids", [])))
        if isinstance(found, Found):
            accepted.append((item, found))
        else:
            rejected.append(found)
    return accepted, rejected
