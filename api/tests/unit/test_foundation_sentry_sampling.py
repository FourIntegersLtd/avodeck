"""Sentry's trace sampling rate comes from SENTRY_TRACES_SAMPLE_RATE (default 0.05); the scrubber still runs."""

from __future__ import annotations

import pytest
from sentry_sdk.scrubber import EventScrubber

from app.core import logs

DSN = "https://public@sentry.invalid/1"


def test_the_default_rate_is_five_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    assert logs.sentry_options(DSN, environment="test")["traces_sample_rate"] == 0.05


def test_the_rate_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    assert logs.sentry_options(DSN, environment="test")["traces_sample_rate"] == 0.25


@pytest.mark.parametrize("bad", ["", "lots", "-1", "2"])
def test_a_bad_rate_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", bad)
    assert logs.sentry_options(DSN, environment="test")["traces_sample_rate"] == 0.05


def test_the_scrubber_and_hooks_still_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1")
    options = logs.sentry_options(DSN, environment="test")
    assert isinstance(options["event_scrubber"], EventScrubber)
    assert options["before_send"] is logs.before_send
    assert options["before_breadcrumb"] is logs.before_breadcrumb
