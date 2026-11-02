"""core/config.py: DEV_SKIP_MFA turns the code step off, but only against a local Supabase (rule 10)."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, local_mfa_skip


def test_off_unless_asked_for() -> None:
    assert local_mfa_skip({"SUPABASE_URL": "http://127.0.0.1:55321"}) is False


@pytest.mark.parametrize("url", ["http://127.0.0.1:55321", "http://localhost:54321"])
def test_on_against_a_local_supabase(url: str) -> None:
    assert local_mfa_skip({"DEV_SKIP_MFA": "1", "SUPABASE_URL": url}) is True


@pytest.mark.parametrize("url", ["https://abc.supabase.co", "", "http://127.0.0.1.example.com"])
def test_refused_anywhere_else(url: str) -> None:
    with pytest.raises(ConfigError, match="DEV_SKIP_MFA"):
        local_mfa_skip({"DEV_SKIP_MFA": "1", "SUPABASE_URL": url})


def test_a_bad_setting_stops_the_app_being_built(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setenv("DEV_SKIP_MFA", "maybe")
    with pytest.raises(ConfigError, match="DEV_SKIP_MFA"):
        create_app()


def test_a_bad_setting_never_fails_a_signed_in_request(monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid
    from datetime import UTC, datetime

    from app.core.auth import session as sessions

    monkeypatch.setenv("DEV_SKIP_MFA", "maybe")
    now = datetime.now(UTC)
    row = (
        uuid.uuid4(),
        uuid.uuid4(),
        "a@example.test",
        None,
        "h",
        "aal1",
        False,
        None,
        now,
        None,
        None,
        None,
        False,
        "active",
    )
    result = sessions.from_row(row)
    assert isinstance(result, sessions.Session)
    assert result.aal == "aal1"
