"""A multi-word name stripped to one placeholder leaves no empty word in the prompt (rule 2 still holds)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.core.ai import provenance
from app.features.setup.letter import Found, Letter, Word


def _letter() -> Letter:
    words = [Word(f"p1w{i}", t, i == 3) for i, t in zip((3, 4, 5, 6), ("in", "treating", "Jane", "Smith"))]
    letter = Letter(words)
    fake = lambda text, **_: SimpleNamespace(text=text.replace("Jane Smith", "[PERSON_1]"))  # noqa: E731
    with patch("app.features.setup.letter.pii.strip", side_effect=fake):
        letter.mask(pack=None, names=["Jane Smith"], dates_of_birth=[])  # type: ignore[arg-type]
    return letter


def test_continuation_word_is_not_empty() -> None:
    letter = _letter()
    assert letter.masked["p1w5"] == "[PERSON_1]"
    assert letter.masked["p1w6"] != ""
    assert letter.masked["p1w6"] == "·"


def test_prompt_shows_a_token_for_the_continuation_word() -> None:
    assert "{p1w5}[PERSON_1] {p1w6}·" in _letter().prompt_text()


def test_full_name_as_the_model_sees_it_still_verifies() -> None:
    found = _letter().verify("[PERSON_1]", ["p1w5", "p1w6"])
    assert isinstance(found, Found)


def test_first_word_alone_still_verifies() -> None:
    found = _letter().verify("[PERSON_1]", ["p1w5"])
    assert isinstance(found, Found)


def test_middle_dot_has_no_atoms() -> None:
    assert provenance.atoms("·") == []
