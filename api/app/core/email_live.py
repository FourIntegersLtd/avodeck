"""The live mail sender: Resend over HTTPS, with the words for every template (rule 6 applies).

The key and the sender address come from the cell config. Addresses, links, parameters and response
bodies are never logged or put in an error: only the status code and the exception type.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
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


def render(mail: Mail, language: str = "en-GB") -> tuple[str, str]:
    """The subject and plain-text body for a mail; raises for an unknown template."""
    if mail.template not in TEMPLATES:
        raise ValueError(f"unknown email template {mail.template!r}")
    check_params(mail)
    w = words(language)
    key = mail.template
    body_key = f"{key}.body"
    if key == "case.reassigned":
        body_key = f"{key}.body.{mail.params['side']}"
    allowed = set(TEMPLATE_PARAMS[key]) if key in TEMPLATE_PARAMS else _FREE_PARAMS[key]
    if set(mail.params) != set(allowed):
        raise ValueError(f"parameters do not fit the {key} template")
    return w[f"{key}.subject"].format_map(mail.params), w[body_key].format_map(mail.params)


@dataclass(frozen=True)
class MailSettings:
    api_key: str = field(repr=False)
    sender: str


def mail_settings(env: Mapping[str, str] | None = None) -> MailSettings | None:
    """None without a key (the outbox stand-in is used); a key without a sender is a ConfigError."""
    source = os.environ if env is None else env
    key = source.get("RESEND_API_KEY", "").strip()
    if not key:
        return None
    sender = source.get("EMAIL_FROM", "").strip()
    if not sender:
        raise ConfigError("EMAIL_FROM is not set")
    return MailSettings(api_key=key, sender=sender)


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
    ) -> None:
        self._key = api_key
        self._sender = sender
        self._transport = transport
        self._sleep = sleep
        self._retry_delay = retry_delay

    def __repr__(self) -> str:
        return "ResendMailer()"

    async def send(self, mail: Mail) -> None:
        subject, text = render(mail)
        payload: dict[str, object] = {"from": self._sender, "to": [mail.to], "subject": subject, "text": text}
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
    return ResendMailer(settings.api_key, settings.sender)
