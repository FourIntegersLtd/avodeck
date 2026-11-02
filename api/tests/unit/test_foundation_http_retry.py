"""The one retry helper: Retry-After as seconds or an HTTP date, capped; backoff with bounded jitter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from app.core.email import Mail
from app.core.email_live import ResendMailer
from app.core.http_retry import backoff, parse_retry_after

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=UTC)


def test_retry_after_in_seconds() -> None:
    assert parse_retry_after("7", cap=60) == 7.0
    assert parse_retry_after(" 2.5 ", cap=60) == 2.5


def test_retry_after_as_an_http_date() -> None:
    assert parse_retry_after(format_datetime(NOW + timedelta(seconds=30), usegmt=True), cap=60, now=NOW) == 30.0


def test_retry_after_in_the_past_is_zero() -> None:
    assert parse_retry_after(format_datetime(NOW - timedelta(minutes=5), usegmt=True), cap=60, now=NOW) == 0.0


def test_a_huge_retry_after_is_capped() -> None:
    assert parse_retry_after("86400", cap=60) == 60.0
    assert parse_retry_after(format_datetime(NOW + timedelta(days=3), usegmt=True), cap=60, now=NOW) == 60.0


@pytest.mark.parametrize("value", [None, "", "soon", "-5", "nan", "inf"])
def test_a_missing_or_unreadable_retry_after_is_none(value: str | None) -> None:
    assert parse_retry_after(value, cap=60) is None


def test_backoff_grows_and_is_capped() -> None:
    for attempt in range(8):
        nominal = min(2.0 * 2**attempt, 30.0)
        for _ in range(50):
            wait = backoff(attempt, 2.0, 30.0)
            assert nominal * 0.5 <= wait <= nominal


def test_backoff_jitter_uses_the_random_source() -> None:
    assert backoff(1, 2.0, 30.0, rng=lambda: 0.0) == 2.0
    assert backoff(1, 2.0, 30.0, rng=lambda: 1.0) == 4.0


async def test_a_resend_retry_carries_the_same_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500) if len(seen) == 1 else httpx.Response(200)

    async def no_sleep(_: float) -> None:
        return None

    mailer = ResendMailer("k", "a@example.test", transport=httpx.MockTransport(handler), sleep=no_sleep)
    await mailer.send(Mail("b@example.test", "mfa.recovery_code_used"))
    first, second = (r.headers.get("idempotency-key") for r in seen)
    assert first and first == second
    assert len(first) <= 256


async def test_two_sends_use_two_keys() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    mailer = ResendMailer("k", "a@example.test", transport=httpx.MockTransport(handler))
    await mailer.send(Mail("b@example.test", "mfa.recovery_code_used"))
    await mailer.send(Mail("b@example.test", "mfa.recovery_code_used"))
    assert seen[0].headers["idempotency-key"] != seen[1].headers["idempotency-key"]


async def test_resend_waits_for_a_429_retry_after_date_capped() -> None:
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not waits and len(handler.calls) == 0:  # type: ignore[attr-defined]
            handler.calls.append(1)  # type: ignore[attr-defined]
            return httpx.Response(429, headers={"Retry-After": "86400"})
        return httpx.Response(200)

    handler.calls = []  # type: ignore[attr-defined]

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    mailer = ResendMailer("k", "a@example.test", transport=httpx.MockTransport(handler), sleep=sleep)
    await mailer.send(Mail("b@example.test", "mfa.recovery_code_used"))
    assert waits and 0 < waits[0] <= 60
