"""The transcript document helpers (transcript-document plan, step 0.3): the numbering, the year and month
grouping, the summary table and the appendix letters that the PDF and the Word copy share.

Entries are built by hand, so nothing here needs a database.
"""

from __future__ import annotations

import inspect
from datetime import date

from app.features.reader import transcript_docx, transcript_pdf
from app.features.reader.transcript_document import appendix_letters, by_year, numbered, summary
from app.features.reader.transcript_layout import Coverage, Layout, Section
from app.features.reader.transcript_words import month_name, show_date, word
from tests.unit.test_reader_transcript_layout import entry

COVERAGE = Coverage(total_pages=0, with_entries=0, no_entries=0, unreadable=0, set_aside=0)


def layout(*sections: Section, none: list[int] | None = None, unread: list[int] | None = None) -> Layout:
    return Layout(list(sections), none or [], unread or [], COVERAGE)


def test_sections_are_numbered_from_one() -> None:
    first, second = Section("a", "t.first"), Section("b", "t.second")
    assert numbered(layout(first, second)) == [(1, first), (2, second)]


def test_years_follow_the_given_order_with_the_undated_last_and_months_in_order() -> None:
    entries = [
        entry(1, day=date(2019, 1, 5)),
        entry(2, day=date(2019, 1, 9)),
        entry(3, day=date(2019, 4, 2)),
        entry(4, day=date(2021, 6, 1)),
        entry(5),
        entry(6),
    ]
    got = by_year(entries)
    assert [y.year for y in got] == [2019, 2021, None]
    assert [(m.month, [e.page_no for e in m.entries]) for m in got[0].months] == [(1, [1, 2]), (4, [3])]
    assert [(m.month, [e.page_no for e in m.entries]) for m in got[1].months] == [(6, [4])]
    assert [(m.month, [e.page_no for e in m.entries]) for m in got[2].months] == [(None, [5, 6])]


def test_nothing_gives_no_years() -> None:
    assert by_year([]) == []


def test_the_summary_counts_entries_and_pages_and_gives_the_dates_in_the_packs_order() -> None:
    dated = Section(
        "a", "transcript.section.own_record",
        [entry(1, day=date(2019, 3, 1)), entry(1, day=date(2019, 7, 2)), entry(2, day=date(2021, 1, 5)), entry(3)],
    )  # fmt: skip
    undated = Section("b", "transcript.section.other_entries", [entry(9)])
    rows = summary(layout(dated, undated), date_order="DMY", language="en")
    assert [r.number for r in rows] == [1, 2]
    assert (rows[0].label, rows[0].entries, rows[0].pages) == ("Own record", 4, 3)
    assert rows[0].date_from == show_date(date(2019, 3, 1), "DMY", "en") == "01/03/2019"
    assert rows[0].date_to == "05/01/2021"
    assert (rows[1].date_from, rows[1].date_to) == ("", "")


def test_appendix_letters_follow_the_appendices_that_exist() -> None:
    assert appendix_letters(layout(Section("a", "t"), none=[4], unread=[7])) == {
        "no_entries": "A",
        "unread": "B",
        "pictures": "C",
    }
    assert appendix_letters(layout(Section("a", "t"), none=[4])) == {"no_entries": "A", "pictures": "B"}
    assert appendix_letters(layout(Section("a", "t"), unread=[7])) == {"unread": "A", "pictures": "B"}
    assert appendix_letters(layout(Section("a", "t"))) == {"pictures": "A"}


def test_month_names_come_from_the_words_table() -> None:
    assert [month_name("en", m) for m in (1, 3, 12)] == ["January", "March", "December"]


def test_the_shared_words_exist() -> None:
    assert word("en", "page_x_of_y", page=2, total=9) == "Page 2 of 9"
    assert word("en", "appendix", letter="A") == "Appendix A"
    assert word("en", "bundle_pages", count=40) == "40 pages in the bundle"
    assert word("en", "not_checked_short") == "Not checked by the expert"
    assert word("en", "col_number") == "No."


def test_both_file_builders_take_the_date_prepared() -> None:
    for build in (transcript_pdf.build_pdf, transcript_docx.build):
        assert inspect.signature(build).parameters["prepared_on"].default is None
