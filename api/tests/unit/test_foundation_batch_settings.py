"""The three page-batch settings (ADR 0038) and the window check at worker start.

`PIPELINE_BATCH_PAGES` (default 0: off, today's single job), `PIPELINE_BATCH_WINDOW` (default 2) and
`PIPELINE_BATCH_MIN_PAGES` (default: the pages setting). The window must be at most `WORKER_CONCURRENCY - 1`; the
check at worker start only warns, with the numbers.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.core.config import ConfigError, pipeline_batch_settings
from app.worker.main import check_pipeline_window

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_the_defaults_are_off_with_a_window_of_two() -> None:
    settings = pipeline_batch_settings({})
    assert (settings.pages, settings.window, settings.min_pages) == (0, 2, 0)
    assert settings.enabled is False


def test_the_minimum_follows_the_batch_size_unless_set() -> None:
    assert pipeline_batch_settings({"PIPELINE_BATCH_PAGES": "100"}).min_pages == 100
    both = pipeline_batch_settings({"PIPELINE_BATCH_PAGES": "100", "PIPELINE_BATCH_MIN_PAGES": "250"})
    assert (both.pages, both.min_pages, both.enabled) == (100, 250, True)


def test_the_window_is_read_from_the_environment() -> None:
    assert pipeline_batch_settings({"PIPELINE_BATCH_WINDOW": "3"}).window == 3


@pytest.mark.parametrize(
    "env",
    [
        {"PIPELINE_BATCH_PAGES": "-1"},
        {"PIPELINE_BATCH_PAGES": "many"},
        {"PIPELINE_BATCH_WINDOW": "0"},
        {"PIPELINE_BATCH_MIN_PAGES": "-5"},
    ],
)
def test_a_bad_value_is_refused(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        pipeline_batch_settings(env)


def test_a_window_above_the_concurrency_less_one_warns_with_the_numbers(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert (
            check_pipeline_window({"PIPELINE_BATCH_PAGES": "100", "PIPELINE_BATCH_WINDOW": "4"}, concurrency=4)
            is not None
        )
    assert "window 4" in caplog.text and "concurrency 4" in caplog.text


def test_a_window_within_the_limit_or_a_flag_that_is_off_says_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert (
            check_pipeline_window({"PIPELINE_BATCH_PAGES": "100", "PIPELINE_BATCH_WINDOW": "3"}, concurrency=4) is None
        )
        assert check_pipeline_window({"PIPELINE_BATCH_WINDOW": "9"}, concurrency=4) is None, "off: nothing to check"
    assert caplog.text == ""


def test_the_settings_are_documented_in_the_env_example() -> None:
    text = ENV_EXAMPLE.read_text()
    for name in ("PIPELINE_BATCH_PAGES=0", "PIPELINE_BATCH_WINDOW=2", "PIPELINE_BATCH_MIN_PAGES"):
        assert name in text
