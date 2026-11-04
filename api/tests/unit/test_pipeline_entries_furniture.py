"""Slice A: page furniture, a birth date and a neighbouring entry must not date an entry (PR 241 review).

The prompt makes entries cover every word, so the first entry of a page holds the page header and the last
holds the footer. When unsure the entry is marked `please_check` (or left undated), never dated confidently.
Runs under the launch and the fixture packs; the words are made up."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.packs import MergedPack
from app.features.pipeline import dates, extract
from tests.support.packs import BOTH, Combo
from tests.unit.test_pipeline_entries import ANCHOR, first_word_of_line, laid_out, pack_of, read_in, whole


def seen(pack: MergedPack) -> dt.date:
    return read_in(pack, dt.date(2020, 1, 12), dt.date(2020, 12, 1))


def birth_of(pack: MergedPack) -> dt.date:
    return read_in(pack, dt.date(1960, 3, 5), dt.date(1960, 5, 3))


def entries_by_line(ordered: list[tuple[str, str]], lines: dict[str, int]) -> list[extract.Entry]:
    """One entry per printed line, together covering every word."""
    firsts = [first_word_of_line(lines, n) for n in sorted(set(lines.values()))]
    ends = [*(f - 1 for f in firsts[1:]), len(ordered)]
    return [
        extract.Entry("k", ordered[a - 1][0], ordered[b - 1][0], a, b, None, None)
        for a, b in zip(firsts, ends, strict=True)
    ]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_date_in_the_page_header_does_not_date_the_first_entry(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Printed on 04/11/2024 Page 3 of 10", "12/01/2020 Surgery consultation cough"])
    got = extract.entry_date(whole(ordered), ordered, set(), pack, lines=lines, page_size=len(ordered))
    assert got == (None, dates.PLEASE_CHECK)
    repaired = extract.repair_entry_date(
        whole(ordered), ordered, set(), lines, pack, dates_of_birth=[], anchor=ANCHOR, page_size=len(ordered)
    )
    assert repaired == (None, dates.PLEASE_CHECK)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_birth_date_on_the_first_line_does_not_date_an_entry_with_a_later_visit_date(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(
        ["Mr Smith DOB 05/03/1960", "12/01/2020 Surgery consultation cough", "Dry cough for two days"]
    )
    size = len(ordered)
    unknown = extract.entry_date(whole(ordered), ordered, set(), pack, lines=lines, page_size=size)
    assert unknown == (None, dates.PLEASE_CHECK), "no date of birth is recorded: two dates, so the reader checks"
    known = extract.entry_date(
        whole(ordered), ordered, set(), pack, dates_of_birth=[birth_of(pack)], lines=lines, page_size=size
    )
    assert known == (seen(pack), dates.PRINTED)
    two, two_lines = laid_out(["Mr Smith DOB 05/03/1960", "12/01/2020 Surgery consultation cough"])
    last = extract.entry_date(
        whole(two), two, set(), pack, dates_of_birth=[birth_of(pack)], lines=two_lines, page_size=len(two)
    )
    assert last == (None, dates.PLEASE_CHECK), "a lone date on the page's last line may be a footer: the reader checks"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_print_date_in_the_page_footer_does_not_date_the_last_entry(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Seen 12/01/2020 cough", "Telephone call mother worried", "Printed on 04/11/2024"])
    carried = whole(ordered, first=first_word_of_line(lines, 2))
    got = extract.entry_date(carried, ordered, set(), pack, lines=lines, page_size=len(ordered))
    assert got == (None, dates.PLEASE_CHECK)
    only, only_lines = laid_out(["Telephone call mother worried again", "Printed on 04/11/2024"])
    alone = extract.entry_date(whole(only), only, set(), pack, lines=only_lines, page_size=len(only))
    assert alone == (None, dates.PLEASE_CHECK)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_an_entry_with_its_own_date_is_still_dated_beside_a_footer(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Intro words", "Seen 12/01/2020 cough", "Printed on 04/11/2024"])
    second = whole(ordered, first=first_word_of_line(lines, 2))
    got = extract.entry_date(second, ordered, set(), pack, lines=lines, page_size=len(ordered))
    assert got == (seen(pack), dates.PRINTED), "the entry's own heading is on its first line"
    reached, reached_lines = laid_out(["Seen 12/01/2020 cough", "Telephone call mother worried"])
    short = extract.entry_date(whole(reached), reached, set(), pack, lines=reached_lines, page_size=len(reached) + 9)
    assert short == (seen(pack), dates.PRINTED), "an entry that does not reach the page end is not guarded"
    single, single_lines = laid_out(["Seen 12/01/2020 cough", "Review 11/02/2020"])
    last = whole(single, first=first_word_of_line(single_lines, 2))
    got_last = extract.entry_date(last, single, set(), pack, lines=single_lines, page_size=len(single))
    assert got_last == (read_in(pack, dt.date(2020, 2, 11), dt.date(2020, 11, 2)), dates.PRINTED)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_repair_never_borrows_a_date_that_belongs_to_another_entry(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Seen 12/01/2020 cough", "Review 11/02/2020", "Telephone call mother worried"])
    entries = entries_by_line(ordered, lines)
    got = extract.repair_entry_date(
        entries[2], ordered, set(), lines, pack, dates_of_birth=[], anchor=ANCHOR, page_entries=entries
    )
    assert got == (None, None)
    uncovered = extract.repair_entry_date(
        entries[2], ordered, set(), lines, pack, dates_of_birth=[], anchor=ANCHOR, page_entries=[entries[2]]
    )
    assert uncovered[1] == dates.LINE_ABOVE, "words no entry covers can still lend their date"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_birth_date_is_matched_on_its_printed_day_month_and_two_digit_year(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Mr Smith DOB 05/03/23 seen today"])
    got = extract.repair_entry_date(
        whole(ordered), ordered, set(), lines, pack, dates_of_birth=[dt.date(1923, 3, 5)], anchor=dt.date(2024, 1, 1)
    )
    assert got == (None, None), "a century guess of the birth date is not a visit date"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_short_year_birth_date_does_not_block_the_visit_date(combo: Combo) -> None:
    pack = pack_of(combo)
    ordered, lines = laid_out(["Mr Smith DOB 05/03/60", "Seen 12/01/2020 cough"])
    got = extract.entry_date(whole(ordered), ordered, set(), pack, [birth_of(pack)], lines)
    assert got == (seen(pack), dates.PRINTED)
    unknown = extract.entry_date(whole(ordered), ordered, set(), pack, [], lines)
    assert unknown == (None, dates.PLEASE_CHECK), "without a recorded birth date the short year still blocks"
