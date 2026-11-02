"""An error report carries no value under a sensitive key, in tags, contexts or anywhere else in the event.

All values are made up.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.scrubber import EventScrubber
from sentry_sdk.transport import Transport

from app.core import logs


class Capture(Transport):
    sent: list[bytes] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        Capture.sent.append(envelope.serialize())


@pytest.fixture
def sentry() -> Iterator[list[bytes]]:
    Capture.sent = []
    logs.init_sentry("https://public@sentry.invalid/1", environment="test", transport=Capture)
    try:
        yield Capture.sent
    finally:
        sentry_sdk.init(dsn=None)


def test_the_options_install_a_recursive_scrubber() -> None:
    scrubber = logs.sentry_options("https://public@sentry.invalid/1", environment="test")["event_scrubber"]
    assert isinstance(scrubber, EventScrubber)


def test_values_under_sensitive_keys_never_leave(sentry: list[bytes]) -> None:
    sentry_sdk.set_tag("password", "hunter2-synthetic")
    sentry_sdk.set_context("login", {"nested": {"authorization": "Bearer synthetic-token"}, "session": "sess-synthetic"})
    sentry_sdk.capture_message("something failed")
    sentry_sdk.flush()
    assert sentry, "no event was sent"
    wire = b"".join(sentry).decode()
    for secret in ("hunter2-synthetic", "synthetic-token", "sess-synthetic"):
        assert secret not in wire
