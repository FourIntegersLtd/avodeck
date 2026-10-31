"""Rule 7 for slice F: a colleague's spot-check crop and value hide the subject's date of birth in any form."""

from __future__ import annotations

from datetime import date

from app.core.packs import default_library
from app.features.report.spot_checks import masked_value, masked_words, with_birth_dates
from tests.support.packs import LAUNCH

PACK = default_library().combine(*LAUNCH.codes)
RECOGNISE = with_birth_dates(PACK.identifier_recognisers(), [date(1960, 2, 1)], PACK.date_order, PACK.language)


def test_the_value_hides_every_written_form_of_the_date_of_birth() -> None:
    for written in ("1 February 1960", "01 Feb 1960", "01/02/1960"):
        assert "1960" not in masked_value(f"born {written} seen 3 May 2021", [], RECOGNISE)
    assert "2021" in masked_value("born 1 February 1960 seen 3 May 2021", [], RECOGNISE)


def test_the_words_of_a_date_over_several_boxes_are_all_hidden() -> None:
    words = [("w1", "born"), ("w2", "1"), ("w3", "February"), ("w4", "1960"), ("w5", "well")]
    assert masked_words(words, [], RECOGNISE) == {"w2", "w3", "w4"}
