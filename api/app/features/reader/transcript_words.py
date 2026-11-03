"""The few words the Word copy of the records transcript prints, keyed by the pack's language, as slice I does
in `trial/words.py`. Words for the kinds of entry, the author's roles and results come from the packs as label
keys (rule 11); a key with no text here prints as its last part, never as a word of this file.
"""

from __future__ import annotations

from datetime import date

_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Records transcript",
        "page": "Page {number}",
        "undated": "No date",
        "unread": "This page couldn't be read by the computer; look at the picture.",
        "not_checked": (
            "This file has not been checked by the expert. It sets out the words printed on each page, in date "
            "order. It is not a summary and holds no opinion."
        ),
        "contents": "Contents",
        "other_entries": "Other entries",
        "no_entry_pages": "Pages with no entries",
        "no_entry_lead": "These pages have no entries to set out. See the picture of each page.",
        "unread_pages": "Pages the computer couldn't read",
        "coverage": "Every page accounted for",
        "coverage_line": (
            "{total} pages: {with_entries} with entries, {no_entries} with no entries, {unreadable} the computer "
            "couldn't read. {set_aside} pages set aside (copies, replaced or held back) are not included."
        ),
        "received": "Received {date}",
        "pictures": "Pictures of the pages",
        "see_picture": "See page {number}",
        "picture_missing": "Picture not available",
        "about": "About this transcript",
        "purpose": (
            "This transcript sets out the words printed on every page of the records, arranged by section and "
            "then by date, oldest first. Each entry gives the bundle page it comes from."
        ),
        "how_to_use": (
            "Page numbers are the bundle's page numbers. Select a page number to see the picture of that page."
        ),
        "holds": "What each section holds",
        "col_number": "No.",
        "col_section": "Section",
        "col_entries": "Entries",
        "col_pages": "Pages",
        "col_from": "From",
        "col_to": "To",
        "cov_total": "Pages in the bundle",
        "cov_with": "Pages with entries",
        "cov_none": "Pages with no entries",
        "cov_unread": "Pages the computer couldn't read",
        "cov_set_aside": "Pages set aside (copies, replaced or held back)",
        "appendix": "Appendix {letter}",
        "page_x_of_y": "Page {page} of {total}",
        "prepared": "Prepared on {date}",
        "bundle_pages": "{count} pages in the bundle",
        "not_checked_short": "Not checked by the expert",
        "months": "January,February,March,April,May,June,July,August,September,October,November,December",
    },
}


def word(language: str, key: str, **values: object) -> str:
    table = _WORDS.get(language.split("-")[0].lower(), _WORDS["en"])
    return table[key].format(**values)


def month_name(language: str, month: int) -> str:
    return word(language, "months").split(",")[month - 1]


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
