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
    mail = render(Mail("a@example.test", template, params_for(template)))
    assert mail.subject.strip() and mail.text.strip() and mail.html.strip()
    assert "{" not in mail.subject + mail.text + mail.html.split("</style>")[-1], "an unfilled placeholder"


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_a_body_shows_each_link_it_is_given(template: str) -> None:
    params = params_for(template)
    mail = render(Mail("a@example.test", template, params))
    if "link" in params:
        assert params["link"] in mail.text
        assert f'href="{params["link"]}"' in mail.html


def test_an_unknown_template_raises() -> None:
    with pytest.raises(ValueError):
        render(Mail("a@example.test", "no.such_template"))


async def test_an_unknown_template_is_refused_before_any_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    mailer = ResendMailer(KEY, SENDER, transport=httpx.MockTransport(handler))
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
    rendered = render(mail)
    assert body["from"] == SENDER and body["to"] == ["expert@example.test"]
    assert body["subject"] == rendered.subject and body["text"] == rendered.text and body["html"] == rendered.html
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


# ---- the look and the words (eventtz's structure: layout, headline, short paragraphs, one button) ----


def test_every_template_has_a_subject_headline_and_body_and_a_warm_plain_tone() -> None:
    from app.core.email_live import words

    w = words()
    for template in TEMPLATES:
        assert w[f"{template}.subject"].strip() and w[f"{template}.headline"].strip()
        bodies = [v for k, v in w.items() if k.startswith(f"{template}.body")]
        assert bodies, template
        for body in bodies:
            assert 1 <= len([p for p in body.split("\n\n") if p.strip()]) <= 3, f"{template}: short body"
    assert not any("!" in v for v in w.values()), "no exclamation marks"


def test_the_words_name_every_role_and_month() -> None:
    from app.core.auth.permissions import ORG_ROLES
    from app.core.email_live import words

    w = words()
    for role in ORG_ROLES:
        assert w[f"role.{role}"].strip()
    for month in range(1, 13):
        assert w[f"month.{month}"].strip()


def invite_html() -> str:
    return render(Mail("a@example.test", "org.invite", params_for("org.invite"))).html


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_head_is_eventtzs_doctype_lang_charset_viewport_and_title() -> None:
    html = invite_html()
    assert html.startswith("<!DOCTYPE html>") and '<html lang="en">' in html
    assert '<meta charset="utf-8"' in html
    assert '<meta name="viewport" content="width=device-width, initial-scale=1"' in html
    assert "<title>You are invited to join a team on Avodeck</title>" in html


def test_the_outer_and_inner_tables_are_the_centred_600px_layout() -> None:
    html = squash(invite_html())
    presentation = 'role="presentation" cellpadding="0" cellspacing="0" border="0"'
    assert html.count(f'<table {presentation} width="100%"') == 2
    assert "width: 100%; background-color: #ffffff" in html
    assert "max-width: 600px; width: 100%; background-color: #ffffff" in html
    assert '<td align="center" style="padding: 0; margin: 0">' in html


def test_the_header_content_footer_cells_and_the_spacer_have_eventtzs_paddings() -> None:
    html = squash(invite_html())
    assert "padding: 16px 20px; text-align: center" in html, "header cell"
    assert "padding: 8px 20px 24px" in html, "content cell"
    assert "padding: 4px 20px 40px; text-align: center" in html, "footer cell"
    assert "height: 32px; line-height: 32px; font-size: 1px" in html, "bottom spacer row"
    assert "&nbsp;" in html


def test_the_wordmark_is_centred_brand_text_and_there_is_no_image() -> None:
    html = squash(invite_html())
    assert "color: #0F3D2C; font-size: 22px; font-weight: 700; letter-spacing: 0.02em" in html
    assert ">Avodeck</p>" in html or "> Avodeck </p>" in html
    assert "<img" not in html


def test_the_headline_and_paragraphs_have_eventtzs_spacing_with_a_17px_body() -> None:
    html = squash(invite_html())
    assert "margin: 0 0 12px; color: #16201B; font-size: 26px; line-height: 1.25; font-weight: 600" in html
    assert "margin: 0 0 16px; color: #16201B; font-size: 17px; line-height: 1.65" in html
    body_sizes = {int(n) for n in re.findall(r"margin: 0 0 16px; color: #16201B; font-size: (\d+)px", html)}
    assert body_sizes == {17}


def test_the_pill_button_is_eventtzs_and_at_least_52px_tall() -> None:
    html = squash(invite_html())
    assert '<p style="margin: 24px 0 0">' in html
    for piece in (
        f'href="{LINK_PARAM}"',
        "display: inline-block",
        "background: #1E6B4E",
        "color: #ffffff",
        "text-decoration: none",
        "padding: 14px 28px",
        "border-radius: 980px",
        "font-size: 15px",
        "font-weight: 600",
    ):
        assert piece in html, piece
    line_height = int(re.search(r"padding: 14px 28px;[^\"]*line-height: (\d+)px", html).group(1))  # type: ignore[union-attr]
    assert 14 * 2 + line_height >= 52


def test_the_footer_names_avodeck_and_claims_no_company_details() -> None:
    mail = render(Mail("a@example.test", "org.invite", params_for("org.invite")))
    html = squash(mail.html)
    assert "This email was sent by Avodeck." in html
    for invented in ("Privacy", "Terms", "registered", "Ltd", "Eventtz"):
        assert invented not in html and invented not in mail.text


def test_the_brand_ink_and_muted_colours_replace_eventtzs() -> None:
    html = invite_html()
    assert "#0F3D2C" in html and "#16201B" in html and "#5F6964" in html and "#1E6B4E" in html
    assert "background: #0F3D2C" not in html, "the button is the app's green, not the dark brand"
    for theirs in ("#3e1964", "#1d1d1f", "#6e6e73", "#8a8199"):
        assert theirs not in html


def test_the_text_part_has_the_headline_the_paragraphs_and_the_action() -> None:
    mail = render(Mail("a@example.test", "org.invite", params_for("org.invite")))
    assert "<" not in mail.text
    lines = mail.text.rstrip("\n").split("\n")
    assert lines[-1] == "Avodeck" and lines[-2] == "", "the text ends with the company line, as eventtz's does"
    assert mail.text.splitlines()[0] == mail.text.splitlines()[0].strip() and mail.text.splitlines()[0]
    assert LINK_PARAM in mail.text


def test_a_template_with_no_link_gets_a_button_to_the_app_only_when_the_origin_is_known() -> None:
    mail = Mail("a@example.test", "invoice.sent", params_for("invoice.sent"))
    assert "<a " not in render(mail).html
    with_origin = render(mail, app_origin="https://app.example.test")
    assert 'href="https://app.example.test"' in with_origin.html and "https://app.example.test" in with_origin.text


def test_every_substituted_value_is_escaped_in_the_html_and_left_plain_in_the_text() -> None:
    nasty = '<script>alert("x")</script> & Tom\'s'
    params = {**params_for("help.request"), "message": nasty}
    mail = render(Mail("hello@example.test", "help.request", params))
    assert "<script>" not in mail.html and "&lt;script&gt;" in mail.html and "&amp;" in mail.html
    assert nasty in mail.text


def test_a_message_keeps_its_line_breaks_in_the_html() -> None:
    params = {**params_for("help.request"), "message": "one\ntwo\n\nthree"}
    html = render(Mail("hello@example.test", "help.request", params)).html
    assert "one<br>two" in html and "three" in html


def test_dates_and_counts_read_as_words_not_codes() -> None:
    mail = render(Mail("a@example.test", "deletion.certificate", params_for("deletion.certificate")))
    for part in (mail.text, mail.html):
        assert "2 January 2031" in part and "1 February 2031" in part
        assert "2031-01-02T" not in part and "pages:3" not in part
        assert "pages" in part


def test_a_role_code_is_shown_as_its_word() -> None:
    mail = render(Mail("a@example.test", "org.invite", params_for("org.invite")))
    from app.core.email_live import words

    assert words()["role.admin"] in mail.text


async def test_the_request_carries_the_html_part_too() -> None:
    script = Script(httpx.Response(200))
    await mailer(script).send(Mail("a@example.test", "org.invite", params_for("org.invite")))
    assert json.loads(script.requests[0].content)["html"].startswith("<!DOCTYPE html>")


@pytest.mark.parametrize("sender", ["hello@avodeck.com", "Avodeck <hello@avodeck.com>"])
def test_the_sender_may_be_a_bare_address_or_a_name_and_address(sender: str) -> None:
    settings = mail_settings({"RESEND_API_KEY": KEY, "EMAIL_FROM": sender})
    assert settings is not None and settings.sender == sender


@pytest.mark.parametrize("sender", ["Avodeck <>", "Avodeck hello@avodeck.com", "hello", "<hello@avodeck.com> x"])
def test_a_sender_that_is_not_an_address_is_a_config_error(sender: str) -> None:
    with pytest.raises(ConfigError):
        mail_settings({"RESEND_API_KEY": KEY, "EMAIL_FROM": sender})


def test_the_app_origin_comes_from_the_cell_config() -> None:
    settings = mail_settings({**ENV, "APP_ORIGIN": "https://app.example.test, https://other.example.test"})
    assert settings is not None and settings.app_origin == "https://app.example.test"
    other = mail_settings(ENV)
    assert other is not None and other.app_origin is None
