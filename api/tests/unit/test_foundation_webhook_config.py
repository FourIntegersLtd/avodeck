"""core/config.py: inbound webhook secrets come from the cell config, never from code (rule 5).

A cell with no secret set has `None`, so its webhook accepts nothing; the secret is hidden from repr so it
never reaches a log.
"""

from __future__ import annotations

from app.core.config import webhook_settings


def test_reads_the_e_sign_secret_from_the_cell_config() -> None:
    assert webhook_settings({"ESIGN_WEBHOOK_SECRET": " zz-secret "}).esign_secret == "zz-secret"


def test_no_secret_is_none() -> None:
    assert webhook_settings({}).esign_secret is None
    assert webhook_settings({"ESIGN_WEBHOOK_SECRET": "  "}).esign_secret is None


def test_the_secret_never_shows_in_repr() -> None:
    assert "zz-secret" not in repr(webhook_settings({"ESIGN_WEBHOOK_SECRET": "zz-secret"}))
