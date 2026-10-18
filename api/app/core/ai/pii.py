"""Rule 7: identifiers out before AI (architecture 6.1 M9, 6.2.1). Runs before every model call.

Presidio's analyzer (with a blank language pipeline: no model download, no guessing at names)
runs these recognisers over the text:

- the case's name list: full names, "Surname, Given" and each name part, any case;
- the jurisdiction pack's identifiers (`identifiers.py`: identifier numbers with their check
  digit, postcodes, labelled dates of birth, ...);
- known dates of birth in any written form, read in the pack's date order;
- e-mail addresses.

Each found value becomes a placeholder `[KIND_n]`; the same value gets the same placeholder, so the
model can still tell people and numbers apart. Dates of events stay: the model needs them.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cache

import spacy
from presidio_analyzer import AnalyzerEngine, EntityRecognizer, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts, SpacyNlpEngine
from presidio_analyzer.predefined_recognizers import EmailRecognizer

from app.core.packs import MergedPack

logging.getLogger("presidio-analyzer").setLevel(logging.WARNING)

NAME = "NAME"
DATE_OF_BIRTH = "DATE_OF_BIRTH"

# Month names by language, for dates written in words. A language not listed here still has its
# numeric dates of birth removed.
MONTHS = {
    "en": (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ),
}

_NUMERIC_DATE = re.compile(r"(?<![\d/.\-])(\d{1,4})([/.\-])(\d{1,2})\2(\d{2,4})(?!\d|[/.\-]\d)")
_DAY_WORD_YEAR = re.compile(
    r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([^\W\d_]{3,})\.?,?\s+(\d{4}|\d{2})(?!\d)", re.I
)
_WORD_DAY_YEAR = re.compile(r"\b([^\W\d_]{3,})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4}|\d{2})(?!\d)", re.I)


@dataclass(frozen=True)
class Stripped:
    """The text with identifiers replaced, and how many of each kind were removed."""

    text: str = field(repr=False)
    counts: dict[str, int]


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    kind: str
    key: str


class _BlankSpacy(SpacyNlpEngine):
    """A spaCy pipeline with no trained model: tokenising only. Recognisers below do the finding."""

    def __init__(self, language: str) -> None:
        super().__init__(models=[{"lang_code": language, "model_name": f"blank-{language}"}])
        self.nlp = {language: spacy.blank(language)}  # type: ignore[assignment]

    def load(self) -> None:
        return None

    def is_loaded(self) -> bool:
        return True


class _Found(EntityRecognizer):
    """Hands spans found by Avodeck's own recognisers to Presidio."""

    def __init__(self, kind: str, language: str, spans: list[tuple[int, int]]) -> None:
        self._spans = spans
        super().__init__(supported_entities=[kind], name=f"avodeck_{kind.lower()}", supported_language=language)

    def load(self) -> None:
        return None

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None
    ) -> list[RecognizerResult]:
        kind = self.supported_entities[0]
        return [RecognizerResult(kind, start, end, 1.0) for start, end in self._spans]


@cache
def _analyzer(language: str) -> AnalyzerEngine:
    registry = RecognizerRegistry(supported_languages=[language])
    registry.add_recognizer(EmailRecognizer(supported_language=language))
    return AnalyzerEngine(nlp_engine=_BlankSpacy(language), registry=registry, supported_languages=[language])


def _key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())


def _name_patterns(names: Sequence[str]) -> list[tuple[re.Pattern[str], int]]:
    patterns: list[tuple[re.Pattern[str], int]] = []
    for person, name in enumerate(names):
        parts = [p for p in name.split() if len(p.strip(".")) >= 2]
        if not parts:
            continue
        forms = {" ".join(parts)}
        if len(parts) > 1:
            forms.add(f"{parts[-1]}, {' '.join(parts[:-1])}")
        for part in parts:
            forms.add(part)
            forms.update(p for p in part.split("-") if len(p) >= 3)
        for form in forms:
            body = r"\s+".join(re.escape(w) for w in form.split())
            patterns.append((re.compile(rf"(?<!\w){body}(?!\w)", re.I), person))
    return patterns


def _year_matches(written: str, year: int) -> bool:
    return int(written) == year if len(written) == 4 else len(written) == 2 and int(written) == year % 100


def _month(word: str, language: str) -> int | None:
    word = word.casefold().rstrip(".")
    for number, name in enumerate(MONTHS.get(language, ()), start=1):
        if name == word or (len(word) >= 3 and name.startswith(word)):
            return number
    return None


def known_dates(text: str, dates: Sequence[date], order: str, language: str) -> Iterable[tuple[int, int]]:
    if not dates:
        return
    for m in _NUMERIC_DATE.finditer(text):
        first, _, second, third = m.groups()
        if len(first) == 4:
            year, month, day = first, second, third
        else:
            parts = dict(zip(order.replace("Y", ""), (first, second), strict=True))
            year, month, day = third, parts["M"], parts["D"]
        if any(int(month) == d.month and int(day) == d.day and _year_matches(year, d.year) for d in dates):
            yield m.start(), m.end()
    for pattern, day_group, month_group in ((_DAY_WORD_YEAR, 1, 2), (_WORD_DAY_YEAR, 2, 1)):
        for m in pattern.finditer(text):
            month = _month(m.group(month_group), language)
            day, year = int(m.group(day_group)), m.group(3)
            if month and any(month == d.month and day == d.day and _year_matches(year, d.year) for d in dates):
                yield m.start(), m.end()


def _pack_spans(text: str, finder: Callable[[str], list[tuple[int, int, str]]]) -> dict[str, list[tuple[int, int]]]:
    found: dict[str, list[tuple[int, int]]] = {}
    for start, end, kind in finder(text):
        found.setdefault(kind, []).append((start, end))
    return found


def strip(
    text: str,
    *,
    pack: MergedPack,
    names: Sequence[str] = (),
    dates_of_birth: Sequence[date] = (),
) -> Stripped:
    """Replace every identifier in `text` with a placeholder. Never logs or returns the originals."""
    language = pack.language
    person_of: dict[tuple[int, int], int] = {}
    name_spans: list[tuple[int, int]] = []
    for pattern, person in _name_patterns(names):
        for m in pattern.finditer(text):
            name_spans.append((m.start(), m.end()))
            person_of.setdefault((m.start(), m.end()), person)

    found = _pack_spans(text, pack.identifier_recognisers())
    found.setdefault(DATE_OF_BIRTH, []).extend(known_dates(text, dates_of_birth, pack.date_order, language))
    found[NAME] = name_spans
    recognisers: list[EntityRecognizer] = [_Found(kind, language, spans) for kind, spans in found.items() if spans]

    results = _analyzer(language).analyze(text=text, language=language, ad_hoc_recognizers=recognisers)
    spans = [
        _Span(
            r.start,
            r.end,
            r.entity_type,
            f"person{person_of[(r.start, r.end)]}" if r.entity_type == NAME else _key(text[r.start : r.end]),
        )
        for r in results
    ]
    chosen: list[_Span] = []
    for span in sorted(spans, key=lambda s: (-(s.end - s.start), s.start)):
        if all(span.end <= c.start or span.start >= c.end for c in chosen):
            chosen.append(span)
    chosen.sort(key=lambda s: s.start)

    numbers: dict[tuple[str, str], int] = {}
    per_kind: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    out: list[str] = []
    cursor = 0
    for span in chosen:
        ident = (span.kind, span.key)
        if ident not in numbers:
            per_kind[span.kind] += 1
            numbers[ident] = per_kind[span.kind]
        out.append(text[cursor : span.start])
        out.append(f"[{span.kind}_{numbers[ident]}]")
        counts[span.kind] += 1
        cursor = span.end
    out.append(text[cursor:])
    return Stripped(text="".join(out), counts=dict(counts))
