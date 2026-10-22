"""A lane may say which of its entries form a series (lanes.schema.json `series`): entries of the named kinds
whose named fields match are drawn as one bar. Nothing in code names a kind or a field (rule 11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import packs
from tests.support.packs import FIXTURE, copy_packs, write

SERIES = (
    "  - {key: zz_series, kinds: [session],"
    " series: {kinds: [session], same: [duration_minutes], max_gap_days: 30, min_count: 2}}\n"
)


def test_a_lane_can_name_a_series(tmp_path: Path) -> None:
    _, fixtures = copy_packs(tmp_path)
    lanes = fixtures / "specialty/zz-specialty/lanes.yaml"
    write(lanes, lanes.read_text() + SERIES)
    lane = packs.load_library(fixtures).combine(*FIXTURE.codes).lanes()[-1]
    assert lane["series"] == {"kinds": ["session"], "same": ["duration_minutes"], "max_gap_days": 30, "min_count": 2}


@pytest.mark.parametrize(
    "bad",
    [
        "{kinds: [session], same: [duration_minutes], max_gap_days: 30, min_count: 1}",
        "{kinds: [session], same: [], max_gap_days: 30, min_count: 2}",
        "{kinds: [session], same: [duration_minutes], max_gap_days: 30, min_count: 2, colour: red}",
    ],
)
def test_a_badly_written_series_is_refused(tmp_path: Path, bad: str) -> None:
    _, fixtures = copy_packs(tmp_path)
    lanes = fixtures / "specialty/zz-specialty/lanes.yaml"
    write(lanes, lanes.read_text() + f"  - {{key: zz_series, kinds: [session], series: {bad}}}\n")
    with pytest.raises(packs.PackError):
        packs.load_library(fixtures).combine(*FIXTURE.codes)
