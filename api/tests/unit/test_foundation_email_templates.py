"""The certificate of deletion email (architecture 10.9; slice H sends it): patient-free parameters only.

It carries the case's reference id, how many rows went from each table, when, and the date the last
backup holding the case expires. Nothing else may be put in it: no label, name or free text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from app.core.email import DELETION_CERTIFICATE, TEMPLATES, Mail, OutboxMailer, deletion_certificate_mail

pytestmark = pytest.mark.anyio

CASE = uuid.UUID("00000000-0000-4000-8000-000000000001")
DELETED_AT = datetime(2031, 1, 2, 3, 4, 5, tzinfo=UTC)


def certificate() -> Mail:
    return deletion_certificate_mail(
        "expert@example.test",
        case_id=CASE,
        counts={"pages": 3, "facts": 0},
        deleted_at=DELETED_AT,
        backup_expiry_on=date(2031, 2, 1),
    )


def test_the_certificate_is_a_known_template_with_only_patient_free_params() -> None:
    mail = certificate()
    assert DELETION_CERTIFICATE in TEMPLATES
    assert mail.template == DELETION_CERTIFICATE
    assert mail.params == {
        "case_ref": str(CASE),
        "counts": "facts:0,pages:3",
        "deleted_at": "2031-01-02T03:04:05+00:00",
        "backup_expiry_on": "2031-02-01",
    }


async def test_the_outbox_keeps_a_valid_certificate() -> None:
    mailer = OutboxMailer()
    await mailer.send(certificate())
    assert [m.template for m in mailer.outbox] == [DELETION_CERTIFICATE]


@pytest.mark.parametrize(
    "params",
    [
        {
            "case_ref": str(CASE),
            "counts": "pages:3",
            "deleted_at": "2031-01-02T03:04:05+00:00",
            "backup_expiry_on": "2031-02-01",
            "label": "Case Z",
        },
        {
            "case_ref": "Case Z",
            "counts": "pages:3",
            "deleted_at": "2031-01-02T03:04:05+00:00",
            "backup_expiry_on": "2031-02-01",
        },
        {
            "case_ref": str(CASE),
            "counts": "Mrs Example:3",
            "deleted_at": "2031-01-02T03:04:05+00:00",
            "backup_expiry_on": "2031-02-01",
        },
        {"case_ref": str(CASE), "counts": "pages:3", "deleted_at": "yesterday", "backup_expiry_on": "2031-02-01"},
        {"case_ref": str(CASE), "counts": "pages:3", "deleted_at": "2031-01-02T03:04:05+00:00"},
    ],
    ids=["extra_param", "case_ref_not_an_id", "counts_free_text", "deleted_at_not_a_time", "missing_param"],
)
async def test_the_outbox_refuses_a_certificate_with_anything_else(params: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="deletion.certificate"):
        await OutboxMailer().send(Mail("expert@example.test", DELETION_CERTIFICATE, params))
    assert "Mrs Example" not in repr(Mail("expert@example.test", DELETION_CERTIFICATE, params))


def test_counts_name_tables_only() -> None:
    with pytest.raises(ValueError):
        deletion_certificate_mail(
            "expert@example.test",
            case_id=CASE,
            counts={"Mrs Example": 1},
            deleted_at=DELETED_AT,
            backup_expiry_on=date(2031, 2, 1),
        )
