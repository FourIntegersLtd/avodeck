"""The live mail sender (Resend): every template has words, the request is right, failures raise.

A fake HTTP transport answers; nothing reaches the network.
"""

from __future__ import annotations

import json
import re
import uuid

import httpx
import pytest

from app.core.config import ConfigError
from app.core.email import TEMPLATE_PARAMS, TEMPLATES, Mail, OutboxMailer
from app.core.email_live import MailSendError, ResendMailer, mail_settings, mailer_from_env, render
from tests.support.packs import LAUNCH_ROOT

pytestmark = pytest.mark.anyio

KEY = "re_test_key_0123456789"
SENDER = "Avodeck <hello@mail.example.test>"
CASE = str(uuid.UUID("00000000-0000-4000-8000-000000000001"))
LINK_PARAM = "https://app.example.test/invites/" + "a" * 24
ENV = {"RESEND_API_KEY": KEY, "EMAIL_FROM": SENDER}

# Parameters the callers pass to templates that have no checks in TEMPLATE_PARAMS.
OTHER_PARAMS = {
    "email_change.confirm_old": {"link": "https://app.example.test/confirm?token=" + "b" * 24},
    "email_change.confirm_new": {"link": "https://app.example.test/confirm?token=" + "c" * 24},
    "mfa.recovery_code_used": {},
    "notification.new": {"kind": "report_ready"},
    "notification.digest": {"count": "3"},
}
SAMPLES = {
    "case_ref": CASE,
    "counts": "pages:3",
    "deleted_at": "2031-01-02T03:04:05+00:00",
    "backup_expiry_on": "2031-02-01",
    "invoice_no": "INV-1",
    "amount": "10.00",
    "currency": "GBP",
    "code": "123456",
    "org_ref": CASE,
    "role": "admin",
    "link": LINK_PARAM,
    "expires_on": "2031-02-01",
    "link_hours": "1",
    "side": "incoming",
    "grant_ref": CASE,
    "expires_at": "2031-02-01T03:04:05+00:00",
    "job_ref": CASE,
    "from_email": "someone@example.test",
    "topic": "other",
    "message": "Hello",
    "page": "/today",
}


def params_for(template: str) -> dict[str, str]:
    if template in TEMPLATE_PARAMS:
        found = {name: SAMPLES[name] for name in TEMPLATE_PARAMS[template]}
        if template == "account.created":
            found["link"] = "https://app.example.test/reset-password?token=" + "d" * 24
        return found
    return dict(OTHER_PARAMS[template])


def test_every_template_is_covered_by_the_samples() -> None:
    assert set(TEMPLATE_PARAMS) | set(OTHER_PARAMS) == set(TEMPLATES)


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_every_template_has_a_subject_and_body_using_only_its_params(template: str) -> None:
    subject, body = render(Mail("a@example.test", template, params_for(template)))
    assert subject.strip() and body.strip()
    assert "{" not in subject + body, "an unfilled placeholder"


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_a_body_shows_each_link_it_is_given(template: str) -> None:
    params = params_for(template)
    _, body = render(Mail("a@example.test", template, params))
    if "link" in params:
        assert params["link"] in body


def test_an_unknown_template_raises() -> None:
    with pytest.raises(ValueError):
        render(Mail("a@example.test", "no.such_template"))


async def test_an_unknown_template_is_refused_before_any_request() -> None:
    calls: list[httpx.Request] = []
    mailer = ResendMailer(KEY, SENDER, transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)))
    with pytest.raises(ValueError):
        await mailer.send(Mail("a@example.test", "no.such_template"))
    assert calls == []


class Script:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def mailer(script: Script) -> ResendMailer:
    async def no_sleep(_: float) -> None:
        return None

    return ResendMailer(KEY, SENDER, transport=httpx.MockTransport(script), sleep=no_sleep)


async def test_the_request_carries_the_key_the_sender_and_the_words() -> None:
    script = Script(httpx.Response(200, json={"id": "x"}))
    mail = Mail("expert@example.test", "mfa.recovery_code_used")
    await mailer(script).send(mail)
    (request,) = script.requests
    assert request.method == "POST" and str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    subject, text = render(mail)
    assert body["from"] == SENDER and body["to"] == ["expert@example.test"]
    assert body["subject"] == subject and body["text"] == text
    assert "reply_to" not in body


async def test_a_reply_to_is_passed_on() -> None:
    script = Script(httpx.Response(200))
    mail = Mail("hello@example.test", "help.request", params_for("help.request"), reply_to="someone@example.test")
    await mailer(script).send(mail)
    assert json.loads(script.requests[0].content)["reply_to"] == "someone@example.test"


async def test_a_429_then_a_200_sends_once() -> None:
    script = Script(httpx.Response(429), httpx.Response(200))
    await mailer(script).send(Mail("a@example.test", "mfa.recovery_code_used"))
    assert len(script.requests) == 2


async def test_a_500_throughout_raises_after_one_retry() -> None:
    script = Script(httpx.Response(500, text="boom"))
    with pytest.raises(MailSendError) as caught:
        await mailer(script).send(Mail("a@example.test", "mfa.recovery_code_used"))
    assert len(script.requests) == 2
    assert "boom" not in str(caught.value) and "a@example.test" not in str(caught.value)


async def test_a_400_is_not_retried() -> None:
    script = Script(httpx.Response(422, text="bad"))
    with pytest.raises(MailSendError):
        await mailer(script).send(Mail("a@example.test", "mfa.recovery_code_used"))
    assert len(script.requests) == 1


def test_a_timeout_is_set() -> None:
    m = ResendMailer(KEY, SENDER, transport=httpx.MockTransport(Script(httpx.Response(200))))
    assert m.timeout.read is not None and m.timeout.connect is not None


def test_a_key_without_a_sender_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        mail_settings({"RESEND_API_KEY": KEY})
    with pytest.raises(ConfigError):
        mail_settings({"RESEND_API_KEY": KEY, "EMAIL_FROM": "   "})


def test_the_settings_hide_the_key() -> None:
    settings = mail_settings(ENV)
    assert settings is not None and settings.sender == SENDER and KEY not in repr(settings)


def test_no_key_means_no_settings_even_without_a_sender() -> None:
    assert mail_settings({}) is None
    assert mail_settings({"EMAIL_FROM": SENDER}) is None


def test_the_live_mailer_is_chosen_only_when_the_key_is_set() -> None:
    assert isinstance(mailer_from_env(ENV), ResendMailer)
    assert isinstance(mailer_from_env({}), OutboxMailer)
    assert isinstance(mailer_from_env({"RESEND_API_KEY": "  "}), OutboxMailer)
    with pytest.raises(ConfigError):
        mailer_from_env({"RESEND_API_KEY": KEY})


def test_the_default_mailer_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.notifications import default_mailer

    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert isinstance(default_mailer(), OutboxMailer)
    monkeypatch.setenv("RESEND_API_KEY", KEY)
    monkeypatch.setenv("EMAIL_FROM", SENDER)
    assert isinstance(default_mailer(), ResendMailer)


def test_the_words_use_no_forbidden_word() -> None:
    from pathlib import Path

    from app.core import packs

    text = (Path(__file__).parents[2] / "app/core/words/en-GB.json").read_text()
    for word in packs.slice_code_forbidden_words(LAUNCH_ROOT):
        flags = 0 if len(word) <= 3 else re.IGNORECASE
        assert not re.search(rf"\b{re.escape(word)}\b", text, flags), word
