"""core/config.py: the cell's region comes from the environment, never from code (rule 5)."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, cell_settings


def test_reads_the_region_from_the_environment() -> None:
    assert cell_settings({"REGION": "zz"}).region == "zz"


def test_a_missing_region_is_a_clear_error() -> None:
    with pytest.raises(ConfigError, match="REGION"):
        cell_settings({})
