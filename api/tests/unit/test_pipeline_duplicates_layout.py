"""Slice A: copies that lost a repeated footer or header are still copies (planted item 1, F13).

A re-scan can keep a boilerplate header or footer on one scan and lose it on the other. That is not a change of
content, but only when the run is printed on at least three other pages with different content, and holds no
figure. A line that moved (it may have changed which list or which dated entry it sits under), an entry that
is real content, a swapped value or a run seen only on copies of one page still mean the pages are not copies.
Runs under both pack combinations; the words are made up.
"""

from __future__ import annotations

import pytest

from app.features.pipeline import duplicates
from tests.support.packs import BOTH, Combo
from tests.unit.test_pipeline_duplicates_rule import LETTER, pack_of, page

FOOTER = "Confidential record held by the practice for the named patient only".split()
LAB = "Haemoglobin 12 g/L white cell count 8 platelets 250 sample taken by the nurse in clinic today".split()
LAB_SWAPPED = "Haemoglobin 8 g/L white cell count 12 platelets 250 sample taken by the nurse in clinic today".split()
OTHERS = ["alpha", "bravo", "charlie", "delta", "echo"]  # a word that makes each page's content its own


def seen(*pages: list[str]) -> dict[tuple[str, tuple[str, ...]], int]:
    return duplicates.edge_runs(page(p) for p in pages)


def bundle_with(run: list[str], where: str, others: int) -> dict[tuple[str, tuple[str, ...]], int]:
    """The page under test (LETTER with the run) and `others` pages of different content that carry it too."""
    pages = [[*LETTER, *run] if where == "end" else [*run, *LETTER]]
    for n in range(others):
        pages.append([OTHERS[n], *LETTER, *run] if where == "end" else [*run, OTHERS[n], *LETTER])
    return seen(*pages)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_footer_on_three_other_pages_may_be_lost_in_a_rescan(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*LETTER, *FOOTER]
    common = bundle_with(FOOTER, "end", 3)
    assert duplicates.same_content(page(printed), page(LETTER), pack, common)
    assert duplicates.same_content(page(LETTER), page(printed), pack, common)
    assert not duplicates.same_content(page(printed), page(LETTER), pack), "no bundle to compare with"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_header_on_three_other_pages_may_be_lost_in_a_rescan(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*FOOTER, *LETTER]
    assert duplicates.same_content(page(printed), page(LETTER), pack, bundle_with(FOOTER, "start", 3))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_line_on_fewer_than_three_other_pages_is_not_a_footer(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*LETTER, *FOOTER]
    assert not duplicates.same_content(page(printed), page(LETTER), pack, bundle_with(FOOTER, "end", 2))
    added = [*LETTER, "Urgent", "referral", "to", "oncology", "within", "two", "weeks"]
    assert not duplicates.same_content(page(added), page(LETTER), pack, seen(added, LETTER, LETTER, LETTER))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_copies_of_one_page_do_not_make_its_last_line_a_footer(combo: Combo) -> None:
    """The same page filed three times counts once: its own last entry is content, not a footer."""
    pack = pack_of(combo)
    entry = "started amoxicillin 500 mg three times daily".split()
    extra = [*LETTER, *entry]
    common = seen(extra, extra, extra, LETTER)
    assert not duplicates.same_content(page(LETTER), page(extra), pack, common)
    unnumbered = "started the new inhaler as discussed at the review".split()
    extra = [*LETTER, *unnumbered]
    assert not duplicates.same_content(page(LETTER), page(extra), pack, seen(extra, extra, extra, extra, LETTER))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_the_pair_compared_does_not_count_as_a_bundle(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*LETTER, *FOOTER]
    # the page under test and two others make three pages in all: the footer needs three others
    assert not duplicates.same_content(page(printed), page(LETTER), pack, bundle_with(FOOTER, "end", 2))
    assert duplicates.same_content(page(printed), page(LETTER), pack, bundle_with(FOOTER, "end", 3))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_run_with_a_figure_is_never_a_footer(combo: Combo) -> None:
    pack = pack_of(combo)
    for run in ("warfarin 5 mg daily".split(), "allergy penicillin anaphylaxis 2019".split()):
        printed = [*LETTER, *run]
        assert not duplicates.same_content(page(printed), page(LETTER), pack, bundle_with(run, "end", 5))
        head = [*run, *LETTER]
        assert not duplicates.same_content(page(head), page(LETTER), pack, bundle_with(run, "start", 5))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_repeated_line_in_the_middle_of_a_page_is_not_a_footer(combo: Combo) -> None:
    pack = pack_of(combo)
    middle = [*LETTER[:10], *FOOTER, *LETTER[10:]]
    assert not duplicates.same_content(page(middle), page(LETTER), pack, bundle_with(FOOTER, "end", 5))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_footer_does_not_excuse_a_changed_number(combo: Combo) -> None:
    pack = pack_of(combo)
    printed = [*LETTER, *FOOTER]
    other = list(LETTER)
    other[other.index("71")] = "77"
    assert not duplicates.same_content(page(printed), page(other), pack, bundle_with(FOOTER, "end", 5))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_a_line_that_moved_is_not_a_copy(combo: Combo) -> None:
    """Where a line sits can be what it means: its list, or the dated entry it belongs to."""
    pack = pack_of(combo)
    current = (
        "current medication amlodipine 5 mg once daily ramipril 10 mg once daily "
        "medication no longer taken simvastatin 40 mg at night review in clinic"
    ).split()
    stopped = (
        "current medication amlodipine 5 mg once daily medication no longer taken "
        "ramipril 10 mg once daily simvastatin 40 mg at night review in clinic"
    ).split()
    assert not duplicates.same_content(page(current), page(stopped), pack), "moved between two lists"
    dated = (
        "12/05/2024 seen by nurse chest infection amoxicillin 500 mg prescribed "
        "14/06/2024 seen by doctor cough settled review"
    ).split()
    later = (
        "12/05/2024 seen by nurse chest infection 14/06/2024 seen by doctor "
        "amoxicillin 500 mg prescribed cough settled review"
    ).split()
    assert not duplicates.same_content(page(dated), page(later), pack), "moved between two dated entries"
    first, second, third = LETTER[:10], LETTER[10:18], LETTER[18:]
    assert not duplicates.same_content(page(LETTER), page([*first, *third, *second]), pack)
    assert not duplicates.same_content(page(LETTER), page([*first, *third, *second, *second]), pack), "a line twice"
    assert not duplicates.same_content(page(LETTER), page([*first, *third]), pack), "a line lost"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_swapped_values_are_never_a_copy(combo: Combo) -> None:
    """The same words with the figures exchanged between two labels are two different results."""
    pack = pack_of(combo)
    assert not duplicates.same_content(page(LAB), page(LAB_SWAPPED), pack)
    assert duplicates.same_content(page(LAB), page(LAB), pack)


def test_a_page_that_lost_its_footer_is_still_a_likely_pair() -> None:
    """A lost footer takes a fifth of a short page's word runs with it: under 0.8, still worth a word-by-word look."""
    from tests.unit.test_pipeline_duplicates_rule import picture, signature

    pic = picture("lines")
    sigs = [signature(1, [*LETTER, *FOOTER], pic), signature(2, LETTER, pic)]
    similar = duplicates._sketch(sigs[0].words).jaccard(duplicates._sketch(sigs[1].words))
    assert 0.6 <= similar < 0.8
    assert [(a.page_no, b.page_no) for a, b in duplicates.likely_pairs(sigs, new={"page-2"})] == [(1, 2)]
