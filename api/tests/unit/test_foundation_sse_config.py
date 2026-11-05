"""Settings for pushed live updates (PR 8): the listen URL and the safety tick."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, database_settings, sse_settings

API = "postgresql://app_api:x@db.example:6543/postgres"
LISTEN = "postgresql://app_api:x@db.example:5432/postgres"


def test_the_listen_url_is_optional_and_hidden_from_repr() -> None:
    assert database_settings({"DATABASE_URL": API}).url_listen is None
    assert database_settings({"DATABASE_URL": API, "DATABASE_URL_LISTEN": "  "}).url_listen is None
    settings = database_settings({"DATABASE_URL": API, "DATABASE_URL_LISTEN": LISTEN})
    assert settings.url_listen == LISTEN
    assert LISTEN not in repr(settings)


def test_the_safety_tick_defaults_to_thirty_seconds() -> None:
    assert sse_settings({}).safety_poll_seconds == 30.0
    assert sse_settings({"SSE_SAFETY_POLL_SECONDS": "12.5"}).safety_poll_seconds == 12.5


@pytest.mark.parametrize("bad", ["soon", "0", "-3"])
def test_a_bad_safety_tick_stops_start_up_naming_the_variable(bad: str) -> None:
    with pytest.raises(ConfigError, match="SSE_SAFETY_POLL_SECONDS"):
        sse_settings({"SSE_SAFETY_POLL_SECONDS": bad})
