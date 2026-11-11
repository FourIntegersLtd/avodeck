"""Words Avodeck itself writes into slice G's documents (not interface strings, which are i18n keys).

Keyed by the pack's language. No court, party or profession word appears here (rule 11); the declaration
comes from the pack.
"""

from __future__ import annotations

from datetime import date
from typing import Final

_WORDS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "answers": "Answers to written questions {n}",
        "answers_of": "These answers add to {name}, served on {date}.",
        "question": "Question {n}",
        "question_ref": "Question {n} (paragraph {ref})",
        "answer": "Answer",
        "references": "References",
        "reference": "[{n}] p.{page}",
        "supplementary": "Supplementary report {n}",
        "supplementary_of": "This report corrects or updates {name}, served on {date}.",
        "paragraph": "Paragraph {n}",
        "correction": "Correction",
        "change_of_opinion": "Change of opinion",
        "as_served": "As served",
        "now_reads": "Now reads",
        "reason": "Reason",
        "declaration": "Declaration",
        "dated": "Dated {date}",
    },
}

# The words that introduce a page number when one expert cites a page of the records ("page 12", "p.12").
_PAGE_MARKERS: Final[dict[str, tuple[str, ...]]] = {"en": ("page", "pages", "p", "pp")}

_MONTHS: Final[dict[str, tuple[str, ...]]] = {
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}


def _lang(language: str) -> str:
    code = language.split("-")[0].lower()
    return code if code in _WORDS else "en"


def page_markers(language: str) -> tuple[str, ...]:
    return _PAGE_MARKERS[_lang(language)]


def word(language: str, key: str, **values: object) -> str:
    return _WORDS[_lang(language)][key].format(**values)


def long_date(language: str, date_order: str, day: date) -> str:
    """A date written out in the pack's date order ("2 March 2026")."""
    month = _MONTHS[_lang(language)][day.month - 1]
    if date_order.upper().startswith("M"):
        return f"{month} {day.day}, {day.year}"
    if date_order.upper().startswith("Y"):
        return f"{day.year} {month} {day.day}"
    return f"{day.day} {month} {day.year}"
