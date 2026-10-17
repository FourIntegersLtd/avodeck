"""core/config.py: database connection strings come from the cell's environment (ADR 0013)."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, database_settings

API = "postgresql://app_api:s3cret@db.example:6543/postgres"
DIRECT = "postgresql://app_worker:s3cret2@db.example:5432/postgres"


def test_reads_pooled_and_direct_urls_from_the_environment() -> None:
    settings = database_settings({"DATABASE_URL": API, "DATABASE_URL_DIRECT": DIRECT})
    assert settings.url == API
    assert settings.url_direct == DIRECT


def test_direct_url_is_optional_for_the_api() -> None:
    assert database_settings({"DATABASE_URL": API}).url_direct is None


def test_missing_database_url_is_a_clear_error() -> None:
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        database_settings({})


def test_settings_never_show_passwords_when_printed_or_logged() -> None:
    text = repr(database_settings({"DATABASE_URL": API, "DATABASE_URL_DIRECT": DIRECT}))
    assert "s3cret" not in text


def test_the_migrator_url_is_never_read_by_the_app() -> None:
    settings = database_settings({"DATABASE_URL": API, "DATABASE_URL_MIGRATOR": "postgresql://app_migrator:x@h/db"})
    assert "app_migrator" not in {settings.url, settings.url_direct}
