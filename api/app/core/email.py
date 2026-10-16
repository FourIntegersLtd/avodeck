"""Outgoing email. Patient-free content only: a template key and its parameters, never case text.

Each cell will send through Resend; until then `OutboxMailer` is the local stand-in and keeps
mail in memory (tests read `outbox`). Addresses and links are never logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

EMAIL_CHANGE_CONFIRM_OLD = "email_change.confirm_old"
EMAIL_CHANGE_CONFIRM_NEW = "email_change.confirm_new"
RECOVERY_CODE_USED = "mfa.recovery_code_used"
TEMPLATES = frozenset({EMAIL_CHANGE_CONFIRM_OLD, EMAIL_CHANGE_CONFIRM_NEW, RECOVERY_CODE_USED})


@dataclass(frozen=True)
class Mail:
    to: str = field(repr=False)
    template: str
    params: dict[str, str] = field(default_factory=dict, repr=False)


class Mailer(Protocol):
    async def send(self, mail: Mail) -> None: ...


class OutboxMailer:
    """Local stand-in for the email service: keeps every mail in `outbox`."""

    def __init__(self) -> None:
        self.outbox: list[Mail] = []

    async def send(self, mail: Mail) -> None:
        if mail.template not in TEMPLATES:
            raise ValueError(f"unknown email template {mail.template!r}")
        self.outbox.append(mail)
