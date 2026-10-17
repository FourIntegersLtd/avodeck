"""Outgoing email. Patient-free content only: a template key and its parameters, never case text.

Each cell will send through Resend; until then `OutboxMailer` is the local stand-in and keeps
mail in memory (tests read `outbox`). Addresses and links are never logged.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

EMAIL_CHANGE_CONFIRM_OLD = "email_change.confirm_old"
EMAIL_CHANGE_CONFIRM_NEW = "email_change.confirm_new"
RECOVERY_CODE_USED = "mfa.recovery_code_used"
NOTIFICATION_NEW = "notification.new"
NOTIFICATION_DIGEST = "notification.digest"
DELETION_CERTIFICATE = "deletion.certificate"
TEMPLATES = frozenset(
    {
        EMAIL_CHANGE_CONFIRM_OLD,
        EMAIL_CHANGE_CONFIRM_NEW,
        RECOVERY_CODE_USED,
        NOTIFICATION_NEW,
        NOTIFICATION_DIGEST,
        DELETION_CERTIFICATE,
    }
)

_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_COUNTS = re.compile(r"^([a-z][a-z0-9_]{0,62}:[0-9]{1,12})(,[a-z][a-z0-9_]{0,62}:[0-9]{1,12})*$")


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _is_time(value: str) -> bool:
    try:
        return datetime.fromisoformat(value).tzinfo is not None and "T" in value
    except ValueError:
        return False


def _is_date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


# Templates whose parameters are fixed: exactly these, each passing its check (patient-free by shape).
TEMPLATE_PARAMS: dict[str, dict[str, Callable[[str], bool]]] = {
    DELETION_CERTIFICATE: {
        "case_ref": _is_uuid,
        "counts": lambda v: bool(_COUNTS.match(v)),
        "deleted_at": _is_time,
        "backup_expiry_on": _is_date,
    },
}


@dataclass(frozen=True)
class Mail:
    to: str = field(repr=False)
    template: str
    params: dict[str, str] = field(default_factory=dict, repr=False)


def check_params(mail: Mail) -> None:
    """Refuse a mail whose parameters are not exactly its template's (no values are echoed)."""
    checks = TEMPLATE_PARAMS.get(mail.template)
    if checks is None:
        return
    if set(mail.params) != set(checks) or not all(check(mail.params[k]) for k, check in checks.items()):
        raise ValueError(f"parameters do not fit the {mail.template} template")


def deletion_certificate_mail(
    to: str, *, case_id: uuid.UUID, counts: Mapping[str, int], deleted_at: datetime, backup_expiry_on: date
) -> Mail:
    """The certificate of deletion: the case's reference id, rows removed per table, when, and when
    the last backup holding the case expires. Nothing else."""
    if any(not _TABLE.match(name) or n < 0 for name, n in counts.items()):
        raise ValueError("counts name tables only")
    mail = Mail(
        to,
        DELETION_CERTIFICATE,
        {
            "case_ref": str(case_id),
            "counts": ",".join(f"{name}:{counts[name]}" for name in sorted(counts)),
            "deleted_at": deleted_at.isoformat(),
            "backup_expiry_on": backup_expiry_on.isoformat(),
        },
    )
    check_params(mail)
    return mail


class Mailer(Protocol):
    async def send(self, mail: Mail) -> None: ...


class OutboxMailer:
    """Local stand-in for the email service: keeps every mail in `outbox`."""

    def __init__(self) -> None:
        self.outbox: list[Mail] = []

    async def send(self, mail: Mail) -> None:
        if mail.template not in TEMPLATES:
            raise ValueError(f"unknown email template {mail.template!r}")
        check_params(mail)
        self.outbox.append(mail)
