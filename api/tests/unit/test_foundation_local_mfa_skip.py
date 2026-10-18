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
