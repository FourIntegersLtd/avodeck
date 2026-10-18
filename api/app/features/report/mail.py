"""Slice F's mails: an invoice was sent, and a colleague's spot-check code.

The cell mailer (`app.core.email`) accepts only foundation's templates, and has no invoice or spot-check
template and no attachments yet (foundation request in the slice F brief). Until then slice F checks its
own templates' parameters the same way and hands the mail to `ReportServices.mailer`, an outbox that keeps
it. Parameters are ids, numbers and amounts only: never a name, a case label or clinical words (rule 6).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Final

from app.core.email import Mail

INVOICE_SENT: Final = "invoice.sent"
SPOT_CHECK_CODE: Final = "spot_check.code"

_AMOUNT = re.compile(r"^\d{1,10}\.\d{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_NUMBER = re.compile(r"^[A-Za-z0-9/_.-]{1,40}$")
_CODE = re.compile(r"^\d{6}$")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


TEMPLATE_PARAMS: Final[dict[str, dict[str, Callable[[str], bool]]]] = {
    INVOICE_SENT: {
        "case_ref": _is_uuid,
        "invoice_no": lambda v: bool(_NUMBER.match(v)),
        "amount": lambda v: bool(_AMOUNT.match(v)),
        "currency": lambda v: bool(_CURRENCY.match(v)),
    },
    SPOT_CHECK_CODE: {"code": lambda v: bool(_CODE.match(v))},
}


def checked(mail: Mail) -> Mail:
    """The mail, if its parameters are exactly its template's (no values are echoed on refusal)."""
    checks = TEMPLATE_PARAMS.get(mail.template)
    if checks is None:
        raise ValueError(f"unknown email template {mail.template!r}")
    if set(mail.params) != set(checks) or not all(check(mail.params[k]) for k, check in checks.items()):
        raise ValueError(f"parameters do not fit the {mail.template} template")
    return mail


class ReportOutbox:
    """Keeps every slice F mail in `outbox` until the cell mailer takes these templates."""

    KEEP = 1000

    def __init__(self) -> None:
        self.outbox: list[Mail] = []

    async def send(self, mail: Mail) -> None:
        self.outbox.append(checked(mail))
        del self.outbox[: -self.KEEP]
