"""Rule 7: a known date of birth is removed however its month is written (numbers, full names, abbreviations).

Every date is made up. A word that only looks like a month ("Mars") is not one.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.ai import pii
from app.core.packs import MergedPack, default_library
from tests.support.packs import LAUNCH

MARCH_3 = date(1950, 3, 3)
SEPT_3 = date(1950, 9, 3)
MAY_3 = date(1950, 5, 3)
MARCH_14 = date(1969, 3, 14)

WRITTEN = [
    (MARCH_3, "3/3/1950"),
    (MARCH_3, "03/03/1950"),
    (MARCH_3, "03.03.50"),
    (MARCH_3, "1950-03-03"),
    (MARCH_3, "3 March 1950"),
    (MARCH_3, "3rd March 1950"),
    (MARCH_3, "3rd of March 1950"),
    (MARCH_3, "03 MARCH 1950"),
    (MARCH_3, "3 Mar 1950"),
    (MARCH_3, "3 mar. 1950"),
    (MARCH_3, "3 MAR 1950"),
    (MARCH_3, "March 3, 1950"),
    (MARCH_3, "Mar 3rd 1950"),
    (MARCH_3, "MARCH 3 1950"),
    (SEPT_3, "3 September 1950"),
    (SEPT_3, "3 Sept 1950"),
    (SEPT_3, "3 Sept. 1950"),
    (SEPT_3, "3 Sep 1950"),
    (SEPT_3, "Sept 3 1950"),
    (SEPT_3, "SEPT. 3, 1950"),
    (MAY_3, "3 May 1950"),
    (MAY_3, "May 3rd, 1950"),
    (MARCH_14, "14-Mar-1969"),
    (MARCH_14, "14-MAR-1969"),
    (MARCH_14, "14.3.69"),
    (MARCH_14, "(14-Mar-1969)"),
    (MARCH_14, "14.Mar.1969"),
    (MARCH_14, "14/Mar/1969"),
    (MARCH_14, "14 -Mar- 1969"),
    (MARCH_14, "Mar-14-1969"),
    (MARCH_14, "14-March-1969"),
    (MARCH_14, "14 / 03 / 1969"),
]


@pytest.fixture(scope="module")
def pack() -> MergedPack:
    return default_library().combine(*LAUNCH.codes)


@pytest.mark.parametrize(("born", "written"), WRITTEN)
def test_a_known_date_of_birth_does_not_survive_in_any_written_form(pack: MergedPack, born: date, written: str) -> None:
    out = pii.strip(f"Seen today. Born {written}. Review in six weeks.", pack=pack, dates_of_birth=[born])
    assert written.lower() not in out.text.lower(), "a date of birth survived stripping"
    assert "Review in six weeks" in out.text


def test_a_word_that_only_looks_like_a_month_is_not_taken_for_one(pack: MergedPack) -> None:
    text = "Seen on 3 Mars 1950 by the team."
    out = pii.strip(text, pack=pack, dates_of_birth=[MARCH_3])
    assert out.text == text


def test_a_different_day_of_the_same_month_is_an_event_date_and_stays(pack: MergedPack) -> None:
    out = pii.strip("Seen 4 March 1950.", pack=pack, dates_of_birth=[MARCH_3])  # noqa: B005
    assert "4 March 1950" in out.text


@pytest.mark.parametrize("number", range(1, 13))
def test_every_month_is_removed_in_full_and_short_form(pack: MergedPack, number: int) -> None:
    born = date(1948, number, 14)
    for word in (born.strftime("%B"), born.strftime("%b").upper()):
        out = pii.strip(f"Born 14 {word} 1948.", pack=pack, dates_of_birth=[born])
        assert "1948" not in out.text, "a date of birth survived stripping"
