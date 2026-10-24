"""Slice A: which quarter turn stores a page the right way up (the angle Document Intelligence detects).

The angle is how far the content is turned clockwise, in degrees, in (-180, 180]. The page is stored
turned the other way, snapped to 0/90/180/270. A small skew (under about 10 degrees) needs no turn, and
an angle that is not near a quarter turn is not trusted, so the page is left as it came.
"""

from __future__ import annotations

import pytest

from app.features.pipeline.straighten import SKEW_DEGREES, quarter_turn


@pytest.mark.parametrize(
    ("angle", "turn"),
    [
        (0.0, 0),
        (4.0, 0),
        (-9.9, 0),
        (90.0, 270),
        (87.0, 270),
        (-90.0, 90),
        (-93.0, 90),
        (180.0, 180),
        (-180.0, 180),
        (176.0, 180),
        (-172.0, 180),
        (270.0, 90),  # a client that reports 0..360
        (359.0, 0),
    ],
)
def test_the_page_is_turned_back_by_the_nearest_quarter_turn(angle: float, turn: int) -> None:
    assert quarter_turn(angle) == turn


@pytest.mark.parametrize("angle", [25.0, 45.0, -60.0, 135.0, 100.5])
def test_an_angle_not_near_a_quarter_turn_is_not_trusted(angle: float) -> None:
    assert quarter_turn(angle) == 0


def test_a_skew_of_ten_degrees_or_more_from_upright_is_not_a_quarter_turn() -> None:
    assert SKEW_DEGREES == 10.0
    assert quarter_turn(SKEW_DEGREES + 0.5) == 0
