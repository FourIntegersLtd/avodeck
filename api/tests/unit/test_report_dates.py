"""Slice F's "every date is cited" check finds dates written in the expert's words in the pack's
language, and leaves alone numbers that are not dates (doses, intervals, citation markers)."""

from __future__ import annotations

import pytest

from app.features.report.checks import mentions_date


@pytest.mark.parametrize(
    "text",
    [
        "Seen on 12 March 2024.",
        "Seen on 12th Mar 2024.",
        "Seen on 12/03/2024.",
        "Seen on 12.03.24.",
        "Seen on 2024-03-12.",
        "Seen in March 2024.",
        "Seen on March 12, 2024.",
    ],
)
def test_a_written_date_is_found(text: str) -> None:
    assert mentions_date(text, "en")


@pytest.mark.parametrize(
    "text",
    ["Review in 3/52.", "Ferritin 9 ug/L.", "As noted [[cite:12]].", "Seen twice in 2 weeks.", "May be relevant."],
)
def test_numbers_that_are_not_dates_are_left_alone(text: str) -> None:
    assert not mentions_date(text, "en")
