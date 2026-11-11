"""Slice G: the page and paragraph a claim names are read by code from the words, never from a model."""

from __future__ import annotations

import pytest

from app.features.after_serving.text import Found, Letter, Word

MARKERS = ("page", "pages", "p", "pp")


def letter(text: str) -> Letter:
    words = []
    for n, line in enumerate(text.split("\n")):
        words += [Word(f"w{n}_{i}", t, i == 0) for i, t in enumerate(line.split())]
    return Letter(words)


def whole(doc: Letter, start: int, size: int) -> Found:
    run = doc.words[start : start + size]
    return Found([w.id for w in run], " ".join(w.text for w in run), start)


@pytest.mark.parametrize(
    ("sentence", "page"),
    [
        ("Page 2 records ferritin 19.", "2"),
        ("On p.12 they record a plan.", "12"),
        ("See p 7, then more.", "7"),
        ("Page 007 shows it.", "7"),
        ("Page 2 and page 3 differ.", None),
        ("They never name one.", None),
    ],
)
def test_the_page_is_the_one_their_first_page_naming_sentence_cites(sentence: str, page: str | None) -> None:
    doc = letter(sentence)
    assert doc.cited_page(whole(doc, 0, len(doc.words)), MARKERS) == page


def test_sentences_naming_a_page_are_counted_from_the_words_and_paragraph_numbers_are_not_sentences() -> None:
    doc = letter("Report\n1. Page 2 records ferritin 19.\n2. Page 3 records a visit. Nothing else here.\n3. Closing.")
    assert doc.page_sentences(MARKERS) == 2


def test_the_paragraph_is_read_from_the_layout() -> None:
    doc = letter("Report\n3. Page 2 records ferritin 19.")
    assert doc.para_ref(3) == "3"
