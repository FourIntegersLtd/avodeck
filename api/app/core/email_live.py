"""The live mail sender: Resend over HTTPS, with the words for every template (rule 6 applies).

The key and the sender address come from the cell config. Addresses, links, parameters and response
bodies are never logged or put in an error: only the status code and the exception type.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

import httpx

from app.core.config import ConfigError
from app.core.email import TEMPLATE_PARAMS, TEMPLATES, Mail, Mailer, OutboxMailer, check_params

logger = logging.getLogger("app.email")
# httpx logs each request line at INFO; hold it back so a log can never carry more than we choose to.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

RESEND_URL = "https://api.resend.com/emails"
_WORDS = Path(__file__).with_name("words")
# Parameters of templates with no entry in TEMPLATE_PARAMS, as their callers pass them.
_FREE_PARAMS: dict[str, frozenset[str]] = {
    "email_change.confirm_old": frozenset({"link"}),
    "email_change.confirm_new": frozenset({"link"}),
    "mfa.recovery_code_used": frozenset(),
    "notification.new": frozenset({"kind"}),
    "notification.digest": frozenset({"count"}),
}


class MailSendError(RuntimeError):
    """The mail service refused or could not be reached. Carries a status code at most."""


@lru_cache(maxsize=4)
def words(language: str = "en-GB") -> dict[str, str]:
    return json.loads((_WORDS / f"{language}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@dataclass(frozen=True)
class RenderedMail:
    subject: str
    text: str
    html: str = field(repr=False)


# Colours from web/src/foundation/ui/tokens.css (--brand, --ink, --surface-soft, --line); type is large for
# readers who are not computer-confident: nothing under 17px, and a button 52px tall.
_BRAND, _INK, _SOFT, _LINE = "#0F3D2C", "#16201B", "#F6F7F5", "#DDE3DF"
_FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
_DATETIME_PARAMS = frozenset({"deleted_at", "expires_at"})
_DATE_PARAMS = frozenset({"backup_expiry_on", "expires_on"})
_SAFE_URL = re.compile(r"^https?://[^\s<>\"']+$")


def _date(value: date, w: Mapping[str, str]) -> str:
    return w["date"].format(day=value.day, month=w[f"month.{value.month}"], year=value.year)


def _display(params: Mapping[str, str], w: Mapping[str, str]) -> dict[str, str]:
    """Codes turned into words: dates, a count list, a role. Everything else is shown as given."""
    shown = dict(params)
    for name, value in params.items():
        if name in _DATETIME_PARAMS:
            when = datetime.fromisoformat(value).astimezone(UTC)
            shown[name] = w["datetime"].format(date=_date(when.date(), w), time=when.strftime("%H:%M"))
        elif name in _DATE_PARAMS:
            shown[name] = _date(date.fromisoformat(value), w)
        elif name == "counts":
            pairs = (item.split(":") for item in value.split(","))
            shown[name] = ", ".join(f"{table.replace('_', ' ')} {n}" for table, n in pairs)
        elif name == "role":
            shown[name] = w[f"role.{value}"]
    return shown


def _fill(template: str, values: Mapping[str, str], escape: Callable[[str], str]) -> str:
    return template.format_map({k: escape(v) for k, v in values.items()})


def _html_value(value: str) -> str:
    return html.escape(value, quote=True).replace("\n", "<br>")


def _layout(subject: str, headline: str, paragraphs: list[str], action: tuple[str, str] | None, footer: str) -> str:
    p = f"margin: 0 0 18px; color: {_INK}; font-size: 18px; line-height: 1.6"
    body = "".join(f'<p style="{p}">{para}</p>' for para in paragraphs)
    button = ""
    if action is not None:
        label, url = action
        button = (
            f'<p style="margin: 28px 0 0"><a href="{html.escape(url, quote=True)}" style="display: inline-block; '
            f"background-color: {_BRAND}; color: #FFFFFF; text-decoration: none; padding: 0 32px; "
            f'border-radius: 26px; font-size: 18px; font-weight: 600; line-height: 52px; height: 52px">'
            f"{html.escape(label)}</a></p>"
        )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(subject)}</title></head>"
        f'<body style="margin: 0; padding: 0; background-color: {_SOFT}; font-family: {_FONT}; color: {_INK}">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="width: 100%; background-color: {_SOFT}"><tr><td align="center" style="padding: 24px 12px">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="max-width: 600px; width: 100%; background-color: #FFFFFF; border: 1px solid {_LINE}; '
        'border-radius: 12px">'
        f'<tr><td style="padding: 24px 28px 8px"><p style="margin: 0; color: {_BRAND}; font-size: 24px; '
        'font-weight: 700; letter-spacing: 0.02em">Avodeck</p></td></tr>'
        f'<tr><td style="padding: 8px 28px 28px"><h1 style="margin: 0 0 16px; color: {_INK}; font-size: 28px; '
        f'line-height: 1.25; font-weight: 600">{html.escape(headline)}</h1>{body}{button}</td></tr>'
        f'<tr><td style="padding: 16px 28px 24px; border-top: 1px solid {_LINE}">'
        f'<p style="margin: 0; color: {_INK}; font-size: 17px; line-height: 1.5">{html.escape(footer)}</p>'
        "</td></tr></table></td></tr></table></body></html>"
    )


def render(mail: Mail, language: str = "en-GB", *, app_origin: str | None = None) -> RenderedMail:
    """The subject, plain-text part and HTML part for a mail; raises for an unknown template.

    The headline and paragraphs come from the words file; the one button goes to the mail's `link`, or
    else to the app. Every substituted value is escaped in the HTML.
    """
    if mail.template not in TEMPLATES:
        raise ValueError(f"unknown email template {mail.template!r}")
    check_params(mail)
    key = mail.template
    allowed = set(TEMPLATE_PARAMS[key]) if key in TEMPLATE_PARAMS else _FREE_PARAMS[key]
    if set(mail.params) != set(allowed):
        raise ValueError(f"parameters do not fit the {key} template")
    w = words(language)
    variant = mail.params.get("side")
    shown = _display(mail.params, w)

    def pick(name: str) -> str | None:
        return w.get(f"{key}.{name}.{variant}") or w.get(f"{key}.{name}")

    body = pick("body")
    assert body is not None
    plain = [_fill(para, shown, str) for para in body.split("\n\n") if para.strip()]
    rich = [_fill(para, shown, _html_value) for para in body.split("\n\n") if para.strip()]
    subject = _fill(w[f"{key}.subject"], shown, str).replace("\n", " ")
    headline = _fill(w[f"{key}.headline"], shown, str)
    label = pick("action")
    url = mail.params.get("link") or app_origin
    action = (label, url) if label and url and _SAFE_URL.match(url) else None
    lines = [headline, "", *[f"{para}\n" for para in plain]]
    if action:
        lines += [f"{action[0]}: {action[1]}", ""]
    lines.append(w["email.footer"])
    return RenderedMail(
        subject=subject,
        text="\n".join(lines).strip() + "\n",
        html=_layout(subject, headline, rich, action, w["email.footer"]),
    )


_SENDER = re.compile(r"^(?:[^<>@\r\n]{1,100}<[^<>@\s]+@[^<>@\s]+\.[^<>@\s]+>|[^<>@\s]+@[^<>@\s]+\.[^<>@\s]+)$")


@dataclass(frozen=True)
class MailSettings:
    api_key: str = field(repr=False)
    sender: str
    app_origin: str | None = None


def mail_settings(env: Mapping[str, str] | None = None) -> MailSettings | None:
    """None without a key (the outbox stand-in is used); a key without a good sender is a ConfigError.

    `EMAIL_FROM` is a bare address or `Name <address>`. `APP_ORIGIN` (the first, if several) is where a
    mail's button goes when it has no link of its own.
    """
    source = os.environ if env is None else env
    key = source.get("RESEND_API_KEY", "").strip()
    if not key:
        return None
    sender = source.get("EMAIL_FROM", "").strip()
    if not sender:
        raise ConfigError("EMAIL_FROM is not set")
    if not _SENDER.match(sender):
        raise ConfigError("EMAIL_FROM is not an address or Name <address>")
    origin = source.get("APP_ORIGIN", "").split(",")[0].strip().rstrip("/") or None
    return MailSettings(api_key=key, sender=sender, app_origin=origin)


class ResendMailer:
    """Sends through Resend; one retry on 429 and 5xx; raises on the final failure."""

    timeout = httpx.Timeout(10.0, connect=5.0)

    def __init__(
        self,
        api_key: str,
        sender: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_delay: float = 1.0,
        app_origin: str | None = None,
    ) -> None:
        self._key = api_key
        self._sender = sender
        self._transport = transport
        self._sleep = sleep
        self._retry_delay = retry_delay
        self._app_origin = app_origin

    def __repr__(self) -> str:
        return "ResendMailer()"

    async def send(self, mail: Mail) -> None:
        rendered = render(mail, app_origin=self._app_origin)
        payload: dict[str, object] = {
            "from": self._sender,
            "to": [mail.to],
            "subject": rendered.subject,
            "text": rendered.text,
            "html": rendered.html,
        }
        if mail.reply_to:
            payload["reply_to"] = mail.reply_to
        headers = {"Authorization": f"Bearer {self._key}"}
        status = 0
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            for attempt in (1, 2):
                try:
                    response = await client.post(RESEND_URL, json=payload, headers=headers)
                    status = response.status_code
                except httpx.TransportError as exc:
                    status = 0
                    logger.info("mail attempt %d failed: %s", attempt, type(exc).__name__)
                else:
                    logger.info("mail attempt %d status %d", attempt, status)
                    if status < 300:
                        return
                retryable = status == 0 or status == 429 or status >= 500
                if attempt == 1 and retryable:
                    await self._sleep(self._retry_delay)
                    continue
                break
        raise MailSendError(f"mail service answered {status}" if status else "mail service unreachable")


def mailer_from_env(env: Mapping[str, str] | None = None) -> Mailer:
    """The live mailer when the cell has a key, the in-memory outbox otherwise."""
    settings = mail_settings(env)
    if settings is None:
        return OutboxMailer()
    return ResendMailer(settings.api_key, settings.sender, app_origin=settings.app_origin)
