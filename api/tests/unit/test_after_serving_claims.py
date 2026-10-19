"""Rule 2 for the other expert's claims: code checks each claim against the cited page before any model.

`claims.code_check(cited_value, page_words)` gives `not_on_page` when the cited page is not in the bundle,
`matches` with the words when the cited value is a run of the page's words (after normalising case, spacing
and number format), and nothing when code cannot settle it (the model is asked).
"""

from __future__ import annotations

from app.features.after_serving import claims

WORDS = [("w1", "Ferritin"), ("w2", "9"), ("w3", "ug/L"), ("w4", "Haemoglobin"), ("w5", "118"), ("w6", "g/L")]


def test_a_page_that_does_not_exist_is_not_on_page() -> None:
    assert claims.code_check("Ferritin 9", None) == claims.Settled("not_on_page", [])


def test_a_value_in_the_page_words_matches_with_those_words() -> None:
    assert claims.code_check("ferritin  9 UG/L", WORDS) == claims.Settled("matches", ["w1", "w2", "w3"])
    assert claims.code_check("Haemoglobin 118.0 g/L", WORDS) == claims.Settled("matches", ["w4", "w5", "w6"])


def test_anything_else_is_left_for_the_model() -> None:
    assert claims.code_check("ferritin 19", WORDS) is None
    assert claims.code_check("9 Ferritin", WORDS) is None
    assert claims.code_check("", WORDS) is None
