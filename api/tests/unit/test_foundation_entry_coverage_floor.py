"""The share of a page's words an extracted entry must cover is a cell setting, not a constant in a slice."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError
from tests.support.ai import cell


def test_the_default_floor_is_eighty_percent() -> None:
    assert cell().entry_coverage_floor == 0.8


def test_the_floor_can_be_set_from_the_environment() -> None:
    assert cell(AI_ENTRY_COVERAGE_FLOOR="0.65").entry_coverage_floor == 0.65


def test_a_blank_value_keeps_the_default() -> None:
    assert cell(AI_ENTRY_COVERAGE_FLOOR=" ").entry_coverage_floor == 0.8


@pytest.mark.parametrize("bad", ["high", "0", "-0.1", "1.5", "nan"])
def test_a_floor_that_is_not_a_share_between_zero_and_one_is_refused(bad: str) -> None:
    with pytest.raises(ConfigError, match="AI_ENTRY_COVERAGE_FLOOR"):
        cell(AI_ENTRY_COVERAGE_FLOOR=bad)
