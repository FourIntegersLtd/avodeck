"""Slice F's mails in the core email system (rule 6: Resend gets no patient text).

- `invoice.sent`: the case's reference id, the invoice number, the amount and the currency code. No names,
  no case label, no attachment (slice F decision: the expert downloads the invoice).
- `spot_check.code`: the colleague's 6-digit code and nothing else.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.email import (
    INVOICE_SENT,
    SPOT_CHECK_CODE,
    TEMPLATES,
    Mail,
    OutboxMailer,
    invoice_sent_mail,
    spot_check_code_mail,
)

pytestmark = pytest.mark.anyio

CASE = uuid.UUID("00000000-0000-4000-8000-000000000001")
TO = "zz-someone@example.test"


def test_both_templates_are_known() -> None:
    assert {INVOICE_SENT, SPOT_CHECK_CODE} <= TEMPLATES
    assert (INVOICE_SENT, SPOT_CHECK_CODE) == ("invoice.sent", "spot_check.code")


async def test_the_invoice_mail_carries_the_reference_number_and_amount_only() -> None:
    mail = invoice_sent_mail(TO, case_id=CASE, invoice_no="ZZ-12", amount=Decimal("4374"), currency="XTS")
    assert mail.template == INVOICE_SENT
    assert mail.params == {"case_ref": str(CASE), "invoice_no": "ZZ-12", "amount": "4374.00", "currency": "XTS"}
    mailer = OutboxMailer()
    await mailer.send(mail)
    assert mailer.outbox == [mail]


async def test_the_spot_check_mail_carries_the_code_only() -> None:
    mail = spot_check_code_mail(TO, code="012345")
    assert mail.template == SPOT_CHECK_CODE and mail.params == {"code": "012345"}
    await OutboxMailer().send(mail)


@pytest.mark.parametrize(
    ("template", "params"),
    [
        (
            INVOICE_SENT,
            {"case_ref": str(CASE), "invoice_no": "ZZ-1", "amount": "1.00", "currency": "XTS", "name": "Zz"},
        ),
        (INVOICE_SENT, {"case_ref": "Mrs Zz", "invoice_no": "ZZ-1", "amount": "1.00", "currency": "XTS"}),
        (INVOICE_SENT, {"case_ref": str(CASE), "invoice_no": "Mrs Zz Qq", "amount": "1.00", "currency": "XTS"}),
        (INVOICE_SENT, {"case_ref": str(CASE), "invoice_no": "ZZ-1", "amount": "a lot", "currency": "XTS"}),
        (INVOICE_SENT, {"case_ref": str(CASE), "invoice_no": "ZZ-1", "amount": "1.00"}),
        (SPOT_CHECK_CODE, {"code": "12345"}),
        (SPOT_CHECK_CODE, {"code": "123456", "link": "https://example.test/zz"}),
    ],
    ids=["extra", "ref_not_an_id", "number_free_text", "amount_not_money", "missing", "short_code", "code_and_more"],
)
async def test_the_outbox_refuses_anything_else(template: str, params: dict[str, str]) -> None:
    with pytest.raises(ValueError, match=template):
        await OutboxMailer().send(Mail(TO, template, params))


def test_slice_f_sends_through_the_cell_mailer() -> None:
    from app.features.report.services import ReportServices

    assert isinstance(ReportServices().mailer, OutboxMailer)
