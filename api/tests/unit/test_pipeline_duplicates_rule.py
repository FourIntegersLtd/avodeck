"""Slice A increment 5: which pages are copies of earlier pages (planted item 1, F13, Q5).

A copy shows the same typed content as an earlier page: the same words in the same order once case
and edge punctuation are set aside, with the same numbers and dates. Crossed-out words are left out,
and one copy may carry a short handwritten filing note the other lacks. A changed month, name or
value, or a typed line added, means the pages are not copies, so a lab's own print of a result is
never set aside. Runs under both pack combinations; the words are made up.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app.core.packs import MergedPack, default_library
from app.features.pipeline import duplicates
from tests.support.packs import BOTH, Combo

LETTER = (
    "Practice letter about the review appointment on 03/04/2024 . "
    "Blood pressure 128/82 and weight 71 kg were recorded by the nurse . "
    "Please book a further review in six weeks with the practice team"
).split()


def pack_of(combo: Combo) -> MergedPack:
    return default_library().combine(*combo.codes)


def page(
    texts: list[str], handwritten: frozenset[int] = frozenset(), struck: frozenset[int] = frozenset()
) -> list[duplicates.Word]:
    return duplicates.tokens([(t, n in handwritten, n in struck) for n, t in enumerate(texts)])


def changed(at: int, word: str) -> list[str]:
    out = list(LETTER)
    out[at] = word
    return out


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_the_same_words_are_the_same_content(combo: Combo) -> None:
    pack = pack_of(combo)
    shouted = [w.upper() + ("," if n % 5 == 0 else "") for n, w in enumerate(LETTER)]
    assert duplicates.same_content(page(LETTER), page(LETTER), pack)
    assert duplicates.same_content(page(LETTER), page(shouted), pack), "case and edge punctuation are set aside"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_misread_word_is_still_a_copy_but_a_changed_word_is_not(combo: Combo) -> None:
    pack = pack_of(combo)
    at = LETTER.index("appointment")
    assert duplicates.same_content(page(LETTER), page(changed(at, "appointmemt")), pack)
    assert not duplicates.same_content(page(LETTER), page(changed(LETTER.index("nurse"), "doctor")), pack)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_changed_number_or_date_is_never_a_copy(combo: Combo) -> None:
    pack = pack_of(combo)
    assert not duplicates.same_content(page(LETTER), page(changed(LETTER.index("71"), "77")), pack)
    assert not duplicates.same_content(page(LETTER), page(changed(LETTER.index("128/82"), "138/82")), pack)
    assert not duplicates.same_content(page(LETTER), page(changed(LETTER.index("03/04/2024"), "04/03/2024")), pack)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_typed_line_added_is_not_a_copy(combo: Combo) -> None:
    pack = pack_of(combo)
    longer = ["Urgent", "referral", "within", "two", "weeks", *LETTER]
    assert not duplicates.same_content(page(LETTER), page(longer), pack)
    assert not duplicates.same_content(page(longer), page(LETTER), pack)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_short_handwritten_note_on_one_copy_is_still_a_copy(combo: Combo) -> None:
    pack = pack_of(combo)
    noted = [*LETTER, "filed", "JM", "4/9"]
    note = frozenset(range(len(LETTER), len(noted)))
    assert duplicates.same_content(page(noted, handwritten=note), page(LETTER), pack)
    long_note = [*LETTER, *("seen and discussed with the partner today please call".split())]
    assert not duplicates.same_content(
        page(long_note, handwritten=frozenset(range(len(LETTER), len(long_note)))), page(LETTER), pack
    )
    assert not duplicates.same_content(page(noted), page(LETTER), pack), "typed words added are not a note"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_crossed_out_words_are_left_out(combo: Combo) -> None:
    pack = pack_of(combo)
    crossed = [*LETTER[:5], "wrong", "entry", "here", *LETTER[5:]]
    assert duplicates.same_content(page(crossed, struck=frozenset({5, 6, 7})), page(LETTER), pack)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_page_with_few_words_is_never_a_copy(combo: Combo) -> None:
    pack = pack_of(combo)
    short = LETTER[: duplicates.MIN_WORDS - 1]
    assert not duplicates.same_content(page(short), page(short), pack)


def picture(layout: str) -> bytes:
    """A page-shaped picture: typed lines across the page, or columns of a table."""
    img = Image.new("RGB", (200, 283), "white")
    draw = ImageDraw.Draw(img)
    if layout == "lines":
        for y in range(20, 260, 12):
            draw.rectangle((20, y, 180, y + 4), fill=(0, 0, 0))
    else:
        for x in range(10, 200, 40):
            draw.rectangle((x, 0, x + 18, 283), fill=(0, 0, 0))
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


def test_the_picture_hash_fits_a_signed_64_bit_column_and_matches_the_same_picture() -> None:
    a, b = duplicates.picture_hash(picture("lines")), duplicates.picture_hash(picture("lines"))
    assert a == b
    assert -(1 << 63) <= a < 1 << 63
    assert duplicates.picture_hash(picture("table")) != a


def signature(page_no: int, words: list[str], pic: bytes) -> duplicates.Signature:
    toks = page(words)
    return duplicates.Signature(
        page_id=f"page-{page_no}",
        page_no=page_no,
        picture=duplicates.picture_hash(pic),
        words=duplicates.word_hash(toks),
        word_count=len(toks),
    )


def test_likely_pairs_need_close_words_and_a_close_picture_and_a_new_page() -> None:
    same_pic = picture("lines")
    sigs = [
        signature(1, LETTER, same_pic),
        signature(2, LETTER, same_pic),
        signature(3, list(reversed(LETTER)), same_pic),  # other words
        signature(4, LETTER, picture("table")),  # another layout
    ]
    pairs = duplicates.likely_pairs(sigs, new={"page-2", "page-3", "page-4"})
    assert [(a.page_no, b.page_no) for a, b in pairs] == [(1, 2)]
    assert duplicates.likely_pairs(sigs, new=set()) == []
    assert len(duplicates.word_hash(page(LETTER))) == duplicates.NUM_PERM


def test_each_copy_points_at_the_earliest_page_that_is_not_a_copy() -> None:
    numbers = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
    pairs = [("a", "c"), ("b", "c"), ("c", "e"), ("a", "e")]
    # b is already a copy of a; d was kept by the expert after an undo.
    found = duplicates.originals(pairs, numbers, copy_of={"b": "a"}, new={"c", "d", "e"}, kept={"d"})
    assert found == {"c": "a", "e": "a"}
    assert duplicates.originals([("a", "d")], numbers, copy_of={}, new={"d"}, kept={"d"}) == {}
    assert duplicates.originals([("a", "b")], numbers, copy_of={"b": "a"}, new=set(), kept=set()) == {}
