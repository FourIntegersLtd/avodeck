"""The few words the Word copy of the records transcript prints, keyed by the pack's language, as slice I does
in `trial/words.py`. Words for the kinds of entry, the author's roles and results come from the packs as label
keys (rule 11); a key with no text here prints as its last part, never as a word of this file.
"""

from __future__ import annotations

from datetime import date

_WORDS: dict[str, dict[str, str]] = {
    "en": {"title": "Records transcript", "page": "Page {number}", "undated": "No date"},
}


def word(language: str, key: str, **values: object) -> str:
    table = _WORDS.get(language.split("-")[0].lower(), _WORDS["en"])
    return table[key].format(**values)


def label_text(label: str) -> str:
    """A pack label key as plain words: its last part, spaced and capitalised."""
    return label.rsplit(".", 1)[-1].replace("_", " ").capitalize()


def show_date(value: date | None, date_order: str, language: str) -> str:
    """The date in the pack's order (for example day, month, year), or the word for no date."""
    if value is None:
        return word(language, "undated")
    parts = {"D": f"{value.day:02d}", "M": f"{value.month:02d}", "Y": f"{value.year:04d}"}
    order = [ch for ch in date_order.upper() if ch in parts] or ["Y", "M", "D"]
    return "/".join(parts[ch] for ch in order)
