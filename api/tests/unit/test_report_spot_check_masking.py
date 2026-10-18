"""Slice F, colleague spot-checks (review finding 6): a party's name is hidden on the crop and in the value
however short it is and whatever is attached to it ("Wu", "Smith's"), matched as a whole word, ignoring case.
"""

from __future__ import annotations

from app.features.report.spot_checks import MASK, masked_value, masked_words

NAMES = ["Amy Wu", "Jo Smith"]


def nothing(text: str) -> list[tuple[int, int, str]]:
    return []


def test_short_and_possessive_name_words_are_hidden_on_the_crop() -> None:
    words = [
        ("w1", "Seen"),
        ("w2", "by"),
        ("w3", "Wu"),
        ("w4", "with"),
        ("w5", "Smith's"),
        ("w6", "WU."),
        ("w7", "Wuhan"),
        ("w8", "Smithfield"),
    ]
    assert masked_words(words, NAMES, nothing) == {"w3", "w5", "w6"}


def test_short_and_possessive_names_are_hidden_in_the_value() -> None:
    assert masked_value("Seen by Wu with Smith's notes", NAMES, nothing) == f"Seen by {MASK} with {MASK}'s notes"
    assert masked_value("Wuhan and Smithfield", NAMES, nothing) == "Wuhan and Smithfield"
