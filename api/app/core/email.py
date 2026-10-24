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
from decimal import Decimal
from typing import Protocol

from app.core.auth.permissions import ORG_ROLES

EMAIL_CHANGE_CONFIRM_OLD = "email_change.confirm_old"
EMAIL_CHANGE_CONFIRM_NEW = "email_change.confirm_new"
RECOVERY_CODE_USED = "mfa.recovery_code_used"
NOTIFICATION_NEW = "notification.new"
NOTIFICATION_DIGEST = "notification.digest"
DELETION_CERTIFICATE = "deletion.certificate"
INVOICE_SENT = "invoice.sent"
SPOT_CHECK_CODE = "spot_check.code"
ORG_INVITE = "org.invite"
CASE_REASSIGNED = "case.reassigned"
SUPPORT_ACCESS_GRANTED = "support_access.granted"
SUPPORT_JOB_RESUMED = "support.job_resumed"
SUPPORT_JOB_REQUEUED = "support.job_requeued"
TEMPLATES = frozenset(
    {
        EMAIL_CHANGE_CONFIRM_OLD,
        EMAIL_CHANGE_CONFIRM_NEW,
        RECOVERY_CODE_USED,
        NOTIFICATION_NEW,
        NOTIFICATION_DIGEST,
        DELETION_CERTIFICATE,
        INVOICE_SENT,
        SPOT_CHECK_CODE,
        ORG_INVITE,
        CASE_REASSIGNED,
        SUPPORT_ACCESS_GRANTED,
        SUPPORT_JOB_RESUMED,
        SUPPORT_JOB_REQUEUED,
    }
)

_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_AMOUNT = re.compile(r"^\d{1,10}\.\d{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_INVOICE_NO = re.compile(r"^[A-Za-z0-9/_.-]{1,40}$")
_CODE = re.compile(r"^\d{6}$")
# An invite's accept link: the web app's `/invites/<token>` page and nothing else (no query, no names).
_INVITE_LINK = re.compile(r"^(https://[A-Za-z0-9.-]+|http://localhost)(:[0-9]{1,5})?/invites/[A-Za-z0-9_-]{20,128}$")
REASSIGNED_SIDES = frozenset({"outgoing", "incoming"})
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
    # An invoice was sent (slice F): no names and no attachment; the expert downloads the invoice.
    INVOICE_SENT: {
        "case_ref": _is_uuid,
        "invoice_no": lambda v: bool(_INVOICE_NO.match(v)),
        "amount": lambda v: bool(_AMOUNT.match(v)),
        "currency": lambda v: bool(_CURRENCY.match(v)),
    },
    # A colleague's spot-check code (slice F): the code only; the link reaches them from the expert.
    SPOT_CHECK_CODE: {"code": lambda v: bool(_CODE.match(v))},
    # An invitation to join an organisation (slice O). The role is a code the web app turns into words;
    # the organisation's name is shown only once the person has signed in.
    ORG_INVITE: {
        "org_ref": _is_uuid,
        "role": lambda v: v in ORG_ROLES,
        "link": lambda v: bool(_INVITE_LINK.match(v)),
        "expires_on": _is_date,
    },
    # The named expert changed before serving (slice O): sent to the outgoing and the incoming expert.
    CASE_REASSIGNED: {"case_ref": _is_uuid, "side": lambda v: v in REASSIGNED_SIDES},
    # The named expert let support look at one case (slice O): when the access ends.
    SUPPORT_ACCESS_GRANTED: {"case_ref": _is_uuid, "grant_ref": _is_uuid, "expires_at": _is_time},
    # Avodeck support set one of the case's jobs going again (resumed a pause, or requeued a failure).
    SUPPORT_JOB_RESUMED: {"case_ref": _is_uuid, "job_ref": _is_uuid},
    SUPPORT_JOB_REQUEUED: {"case_ref": _is_uuid, "job_ref": _is_uuid},
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


def invoice_sent_mail(to: str, *, case_id: uuid.UUID, invoice_no: str, amount: Decimal, currency: str) -> Mail:
    """An invoice was sent: the case's reference id, the invoice number, the amount and the currency."""
    mail = Mail(
        to,
        INVOICE_SENT,
        {"case_ref": str(case_id), "invoice_no": invoice_no, "amount": f"{amount:.2f}", "currency": currency},
    )
    check_params(mail)
    return mail


def spot_check_code_mail(to: str, *, code: str) -> Mail:
    """The 6-digit code a colleague needs to open a spot-check link."""
    mail = Mail(to, SPOT_CHECK_CODE, {"code": code})
    check_params(mail)
    return mail


def invite_mail(to: str, *, organisation_id: uuid.UUID, role: str, link: str, expires_on: date) -> Mail:
    """An invitation: the organisation's id, the role code, the accept link and the day it expires."""
    mail = Mail(
        to,
        ORG_INVITE,
        {"org_ref": str(organisation_id), "role": role, "link": link, "expires_on": expires_on.isoformat()},
    )
    check_params(mail)
    return mail


def case_reassigned_mail(to: str, *, case_id: uuid.UUID, side: str) -> Mail:
    """The case's named expert changed: its reference id and whether the reader is `outgoing` or `incoming`."""
    mail = Mail(to, CASE_REASSIGNED, {"case_ref": str(case_id), "side": side})
    check_params(mail)
    return mail


def support_access_granted_mail(to: str, *, case_id: uuid.UUID, grant_id: uuid.UUID, expires_at: datetime) -> Mail:
    """Support may look at one case: its reference id, the grant's id and when the access ends."""
    mail = Mail(
        to,
        SUPPORT_ACCESS_GRANTED,
        {"case_ref": str(case_id), "grant_ref": str(grant_id), "expires_at": expires_at.isoformat()},
    )
    check_params(mail)
    return mail


def support_job_mail(to: str, *, template: str, case_id: uuid.UUID, job_id: uuid.UUID) -> Mail:
    """Support resumed or requeued a job: the case's and the job's reference ids only."""
    if template not in (SUPPORT_JOB_RESUMED, SUPPORT_JOB_REQUEUED):
        raise ValueError(f"not a support job template: {template!r}")
    mail = Mail(to, template, {"case_ref": str(case_id), "job_ref": str(job_id)})
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
