"""Words Avodeck itself writes into a report document (not interface strings, which are i18n keys).

They are keyed by the pack's language. Packs do not carry section labels or document words yet (foundation
request in the slice F brief), so a section the expert's template does not name gets its key in plain words.
No court, party or profession word appears here (rule 11); those come from the packs.
"""

from __future__ import annotations

from datetime import date
from typing import Final

_WORDS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "draft": "Draft {n}",
        "report": "Report {n}",
        "supplementary": "Supplementary report {n}",
        "question": "Question {n}",
        "references": "References",
        "pages_read": "Pages read: {read} / {total}",
        "task": "{task} - model {model} - region {region} - prompt version {prompt} - {calls} calls, {first} to {last}",
        "no_tasks": "No automated reading was used.",
        "document": "{name}: pages {first} to {last}",
        "reference": "[{n}] p.{page}",
        "signed_on": "Dated {date}",
    },
}


# Month names (full and short) a written date may use, by language, for the "every date is cited" check.
_MONTHS: Final[dict[str, tuple[str, ...]]] = {
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
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
    ),
}


def months(language: str) -> tuple[str, ...]:
    return _MONTHS.get(language.split("-")[0].lower(), _MONTHS["en"])


def long_date(language: str, date_order: str, day: date) -> str:
    """A date written out for a signed document, in the pack's date order ("18 October 2026")."""
    month = months(language)[day.month - 1].capitalize()
    if date_order.upper().startswith("M"):
        return f"{month} {day.day}, {day.year}"
    if date_order.upper().startswith("Y"):
        return f"{day.year} {month} {day.day}"
    return f"{day.day} {month} {day.year}"


def word(language: str, key: str, **values: object) -> str:
    table = _WORDS.get(language.split("-")[0].lower(), _WORDS["en"])
    return table[key].format(**values)


def section_label(key: str) -> str:
    """A section's key in plain words, for a section the expert's template has no heading for."""
    return key.replace("_", " ").capitalize()
