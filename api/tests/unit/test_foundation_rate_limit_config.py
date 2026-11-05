"""Settings for per-person rate limits and the open-stream cap (scalability PR 12)."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, rate_settings


def test_defaults_are_six_hundred_requests_a_minute_and_three_streams() -> None:
    settings = rate_settings({})
    assert settings.per_minute == 600
    assert settings.streams_per_person == 3


def test_both_can_be_set_from_the_environment() -> None:
    settings = rate_settings({"API_RATE_PER_MINUTE": "120", "API_MAX_STREAMS_PER_PERSON": "5"})
    assert (settings.per_minute, settings.streams_per_person) == (120, 5)


def test_zero_turns_a_limit_off() -> None:
    settings = rate_settings({"API_RATE_PER_MINUTE": "0", "API_MAX_STREAMS_PER_PERSON": "0"})
    assert (settings.per_minute, settings.streams_per_person) == (0, 0)


@pytest.mark.parametrize("name", ["API_RATE_PER_MINUTE", "API_MAX_STREAMS_PER_PERSON"])
@pytest.mark.parametrize("value", ["-1", "many"])
def test_a_negative_or_non_numeric_value_is_refused(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        rate_settings({name: value})
