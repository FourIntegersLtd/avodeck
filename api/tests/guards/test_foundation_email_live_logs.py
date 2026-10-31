"""Rule 6 for the live mail sender: an address, a link, a parameter or a response body never reaches a log."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.core.email import Mail
from app.core.email_live import ResendMailer
from app.core.notifications import send_mail

pytestmark = pytest.mark.anyio

KEY = "re_guard_key_0123456789"
ADDRESS = "zed.quillfeather@example.test"
TOKEN = "q" * 30
LINK = f"https://app.example.test/invites/{TOKEN}"
REPLY = "the.expert@example.test"
BODY_ECHO = "echo-of-the-request-body-zzz"
SECRETS = (ADDRESS, TOKEN, LINK, REPLY, KEY, BODY_ECHO, "someone-else")


def assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, "an address, link, key or body reached a log line"


def mail() -> Mail:
    return Mail(
        ADDRESS,
        "org.invite",
        {"org_ref": "00000000-0000-4000-8000-000000000001", "role": "admin", "link": LINK, "expires_on": "2031-02-01"},
        reply_to=REPLY,
    )


async def no_sleep(_: float) -> None:
    return None


def sender(*responses: httpx.Response) -> ResendMailer:
    queue = list(responses)
    transport = httpx.MockTransport(lambda r: queue.pop(0) if len(queue) > 1 else queue[0])
    return ResendMailer(KEY, "Avodeck <hello@mail.example.test>", transport=transport, sleep=no_sleep)


async def test_a_good_send_logs_no_recipient_link_or_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await sender(httpx.Response(429, text=BODY_ECHO), httpx.Response(200, text=BODY_ECHO)).send(mail())
    assert_clean(caplog.text)


async def test_a_failed_send_logs_the_type_and_nothing_else(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await send_mail(sender(httpx.Response(500, text=f"{BODY_ECHO} {ADDRESS} {LINK}")), mail())
    assert any("email not sent" in r.getMessage() for r in caplog.records)
    assert_clean(caplog.text)


async def test_a_network_error_logs_no_address(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {ADDRESS} {LINK}")

    live = ResendMailer(KEY, "Avodeck <hello@mail.example.test>", transport=httpx.MockTransport(boom), sleep=no_sleep)
    await send_mail(live, mail())
    assert_clean(caplog.text)
