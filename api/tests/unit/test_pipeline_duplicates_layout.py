"""Slice A: copies whose reading order or printed footer differs are still copies (planted item 1, F13).

A re-scan can come out with a block of lines read in another order, or with the same boilerplate footer
(or header) kept on one scan and lost on the other. Neither changes the content. A block that is really
added, taken away or changed, a swapped value, or a footer that is not repeated across the bundle still
means the pages are not copies. Runs under both pack combinations; the words are made up.
"""

from __future__ import annotations

import pytest

from app.features.pipeline import duplicates
from tests.support.packs import BOTH, Combo
from tests.unit.test_pipeline_duplicates_rule import LETTER, pack_of, page

FOOTER = "Confidential record held by the practice for the named patient only".split()
LAB = "Haemoglobin 12 g/L white cell count 8 platelets 250 sample taken by the nurse in clinic today".split()
LAB_SWAPPED = "Haemoglobin 8 g/L white cell count 12 platelets 250 sample taken by the nurse in clinic today".split()


def seen(*pages: list[str]) -> dict[tuple[str, tuple[str, ...]], int]:
    return duplicates.edge_runs(page(p) for p in pages)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_footer_repeated_across_the_bundle_may_be_lost_in_a_rescan(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*LETTER, *FOOTER]
    common = seen(printed, printed, printed)
    assert duplicates.same_content(page(printed), page(LETTER), pack, common)
    assert duplicates.same_content(page(LETTER), page(printed), pack, common)
    assert not duplicates.same_content(page(printed), page(LETTER), pack), "no bundle to compare with"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_header_repeated_across_the_bundle_may_be_lost_in_a_rescan(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*FOOTER, *LETTER]
    assert duplicates.same_content(page(printed), page(LETTER), pack, seen(printed, printed, printed))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_line_that_is_not_repeated_is_not_a_footer(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*LETTER, *FOOTER]
    assert not duplicates.same_content(page(printed), page(LETTER), pack, seen(printed, printed)), "only two pages"
    added = [*LETTER, "Urgent", "referral", "to", "oncology", "within", "two", "weeks"]
    assert not duplicates.same_content(page(added), page(LETTER), pack, seen(added, LETTER, LETTER, LETTER))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_repeated_line_in_the_middle_of_a_page_is_not_a_footer(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*LETTER, *FOOTER]
    middle = [*LETTER[:10], *FOOTER, *LETTER[10:]]
    assert not duplicates.same_content(page(middle), page(LETTER), pack, seen(printed, printed, printed))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_footer_does_not_excuse_a_changed_number(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*LETTER, *FOOTER]
    other = list(LETTER)
    other[other.index("71")] = "77"
    assert not duplicates.same_content(page(printed), page(other), pack, seen(printed, printed, printed))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_lines_read_in_another_order_are_the_same_content(combo: Combo) -> None:
    pack = pack_of(combo)
    first, second, third = LETTER[:10], LETTER[10:18], LETTER[18:]
    moved = [*first, *third, *second]
    assert [*first, *second, *third] == LETTER
    assert duplicates.same_content(page(LETTER), page(moved), pack)
    assert duplicates.same_content(page(moved), page(LETTER), pack)
    assert duplicates.same_content(page(LETTER), page([*second, *first, *third]), pack)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_moved_line_that_changed_is_not_a_copy(combo: Combo) -> None:
    pack = pack_of(combo)
    first, second, third = LETTER[:10], LETTER[10:18], LETTER[18:]
    changed_value = [w.replace("71", "77") for w in second]
    assert not duplicates.same_content(page(LETTER), page([*first, *third, *changed_value]), pack)
    assert not duplicates.same_content(page(LETTER), page([*first, *third, *second, *second]), pack), "a line twice"
    assert not duplicates.same_content(page(LETTER), page([*first, *third]), pack), "a line lost"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_swapped_values_are_never_moved_lines(combo: Combo) -> None:
    """The same words with the figures exchanged between two labels are two different results."""
    pack = pack_of(combo)
    assert not duplicates.same_content(page(LAB), page(LAB_SWAPPED), pack)
    assert duplicates.same_content(page(LAB), page(LAB), pack)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_short_phrase_that_moves_is_not_a_reading_order_change(combo: Combo) -> None:
    pack = pack_of(combo)
    one = "Seen by the nurse . Dose 5 mg daily . Review in 2 weeks with the doctor please and book it now".split()
    two = "Dose 5 mg daily . Seen by the nurse . Review in 2 weeks with the doctor please and book it now".split()
    assert duplicates.same_content(page(one), page(two), pack), "a line of five or more words"
    short_a = "Dose 5 mg Review in 2 weeks with the doctor please and book it now and the nurse".split()
    short_b = "Review in 2 weeks with the doctor please and book it now and the nurse Dose 5 mg".split()
    assert duplicates.same_content(page(short_a), page(short_b), pack), "three words is the shortest line moved"
    pair_a = "Dose 5 Review in 2 weeks with the doctor please and book it now and the nurse".split()
    pair_b = "Review in 2 weeks with the doctor please and book it now and the nurse Dose 5".split()
    assert not duplicates.same_content(page(pair_a), page(pair_b), pack), "two words may be a swapped value"


def test_a_page_that_lost_its_footer_is_still_a_likely_pair() -> None:
    """A lost footer takes a fifth of a short page's word runs with it: under 0.8, still worth a word-by-word look."""
    from tests.unit.test_pipeline_duplicates_rule import picture, signature

    pic = picture("lines")
    sigs = [signature(1, [*LETTER, *FOOTER], pic), signature(2, LETTER, pic)]
    similar = duplicates._sketch(sigs[0].words).jaccard(duplicates._sketch(sigs[1].words))
    assert 0.6 <= similar < 0.8
    assert [(a.page_no, b.page_no) for a, b in duplicates.likely_pairs(sigs, new={"page-2"})] == [(1, 2)]
