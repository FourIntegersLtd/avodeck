"""Settings for pushed live updates (PR 8): the listen URL and the safety tick."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, listen_url, sse_settings

API = "postgresql://app_api:x@db.example:6543/postgres"
LISTEN = "postgresql://app_api:x@db.example:5432/postgres"


def test_the_listen_url_is_optional() -> None:
    assert listen_url({"DATABASE_URL": API}) is None
    assert listen_url({"DATABASE_URL": API, "DATABASE_URL_LISTEN": "  "}) is None
    assert listen_url({"DATABASE_URL": API, "DATABASE_URL_LISTEN": LISTEN}) == LISTEN


def test_the_same_url_as_a_transaction_mode_database_url_is_refused() -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL_LISTEN"):
        listen_url({"DATABASE_URL": API, "DATABASE_URL_LISTEN": API})


def test_any_url_on_the_transaction_mode_port_is_refused_even_on_another_host() -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL_LISTEN"):
        listen_url({"DATABASE_URL_LISTEN": "postgresql://app_api:x@other.example:6543/postgres"})


def test_the_same_session_mode_url_is_fine_when_the_database_url_is_already_session_mode() -> None:
    assert listen_url({"DATABASE_URL": LISTEN, "DATABASE_URL_LISTEN": LISTEN}) == LISTEN


def test_a_listen_url_without_the_pooler_port_is_accepted_whatever_the_database_url() -> None:
    direct = "postgresql://app_api:x@db.example/postgres"
    assert listen_url({"DATABASE_URL_LISTEN": direct}) == direct


def test_the_safety_tick_defaults_to_thirty_seconds() -> None:
    assert sse_settings({}).safety_poll_seconds == 30.0
    assert sse_settings({"SSE_SAFETY_POLL_SECONDS": "12.5"}).safety_poll_seconds == 12.5


@pytest.mark.parametrize("bad", ["soon", "0", "-3"])
def test_a_bad_safety_tick_stops_start_up_naming_the_variable(bad: str) -> None:
    with pytest.raises(ConfigError, match="SSE_SAFETY_POLL_SECONDS"):
        sse_settings({"SSE_SAFETY_POLL_SECONDS": bad})
