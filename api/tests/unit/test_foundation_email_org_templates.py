"""Slice O's mails in the core email system (rule 6: Resend gets no patient text).

Each mail is a template key and parameters whose shape is checked: ids, codes, dates and a link.
Words (the role's name, the greeting) come from the web app's i18n keys for the template, never
from here, and nothing free-text (no case label, no organisation or person name) is sent.

- `org.invite`: the organisation's id, the role code, the accept link (`/invites/<token>`) and the
  day the invite expires.
- `case.reassigned`: the case's reference id and which side of the change the reader is on.
- `support_access.granted`: the case's reference id, the grant's id and when it ends.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from app.core.email import (
    CASE_REASSIGNED,
    ORG_INVITE,
    SUPPORT_ACCESS_GRANTED,
    TEMPLATES,
    Mail,
    OutboxMailer,
    case_reassigned_mail,
    invite_mail,
    support_access_granted_mail,
)

pytestmark = pytest.mark.anyio

ORG = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
CASE = uuid.UUID("00000000-0000-4000-8000-000000000001")
GRANT = uuid.UUID("00000000-0000-4000-8000-0000000000bb")
TO = "zz-someone@example.test"
LINK = "https://app.example.test/invites/AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def test_the_templates_are_known() -> None:
    assert {ORG_INVITE, CASE_REASSIGNED, SUPPORT_ACCESS_GRANTED} <= TEMPLATES
    assert (ORG_INVITE, CASE_REASSIGNED, SUPPORT_ACCESS_GRANTED) == (
        "org.invite",
        "case.reassigned",
        "support_access.granted",
    )


async def test_the_invite_carries_ids_the_role_code_the_link_and_the_expiry_only() -> None:
    mail = invite_mail(TO, organisation_id=ORG, role="assistant", link=LINK, expires_on=date(2026, 10, 29))
    assert mail.template == ORG_INVITE
    assert mail.params == {"org_ref": str(ORG), "role": "assistant", "link": LINK, "expires_on": "2026-10-29"}
    mailer = OutboxMailer()
    await mailer.send(mail)
    assert mailer.outbox == [mail]


@pytest.mark.parametrize("side", ["outgoing", "incoming"])
async def test_the_reassignment_mail_carries_the_case_reference_and_side_only(side: str) -> None:
    mail = case_reassigned_mail(TO, case_id=CASE, side=side)
    assert (mail.template, mail.params) == (CASE_REASSIGNED, {"case_ref": str(CASE), "side": side})
    await OutboxMailer().send(mail)


async def test_the_support_grant_mail_carries_the_case_grant_and_end_only() -> None:
    ends = datetime(2026, 10, 23, 9, 30, tzinfo=UTC)
    mail = support_access_granted_mail(TO, case_id=CASE, grant_id=GRANT, expires_at=ends)
    assert mail.template == SUPPORT_ACCESS_GRANTED
    assert mail.params == {"case_ref": str(CASE), "grant_ref": str(GRANT), "expires_at": ends.isoformat()}
    await OutboxMailer().send(mail)


def test_the_role_is_an_organisation_role_code() -> None:
    with pytest.raises(ValueError):
        invite_mail(TO, organisation_id=ORG, role="Dr Zz Qq", link=LINK, expires_on=date(2026, 10, 29))
    with pytest.raises(ValueError):
        invite_mail(TO, organisation_id=ORG, role="superuser", link=LINK, expires_on=date(2026, 10, 29))


@pytest.mark.parametrize(
    "link",
    [
        "https://app.example.test/cases/AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        "https://app.example.test/invites/short",
        "https://app.example.test/invites/AbCdEfGhIjKlMnOpQrStUvWxYz012345?name=Zz Qq",
        "javascript:alert(1)",
        "ftp://app.example.test/invites/AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    ],
)
def test_the_invite_link_is_only_an_accept_link(link: str) -> None:
    with pytest.raises(ValueError):
        invite_mail(TO, organisation_id=ORG, role="viewer", link=link, expires_on=date(2026, 10, 29))


def test_reassignment_sides_are_codes() -> None:
    with pytest.raises(ValueError):
        case_reassigned_mail(TO, case_id=CASE, side="Mrs Zz")


def test_a_grant_end_needs_its_time_zone() -> None:
    with pytest.raises(ValueError):
        support_access_granted_mail(TO, case_id=CASE, grant_id=GRANT, expires_at=datetime(2026, 10, 23, 9, 30))


@pytest.mark.parametrize(
    ("template", "params"),
    [
        (ORG_INVITE, {"org_ref": str(ORG), "role": "viewer", "link": LINK, "expires_on": "2026-10-29", "name": "Zz"}),
        (ORG_INVITE, {"org_ref": "Zz Agency", "role": "viewer", "link": LINK, "expires_on": "2026-10-29"}),
        (CASE_REASSIGNED, {"case_ref": "Mrs Zz", "side": "incoming"}),
        (CASE_REASSIGNED, {"case_ref": str(CASE), "side": "incoming", "label": "Zz v Qq"}),
        (SUPPORT_ACCESS_GRANTED, {"case_ref": str(CASE), "grant_ref": str(GRANT), "expires_at": "tomorrow"}),
    ],
)
async def test_the_outbox_refuses_any_other_parameters(template: str, params: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        await OutboxMailer().send(Mail(TO, template, params))
