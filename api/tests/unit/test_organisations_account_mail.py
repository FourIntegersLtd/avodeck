"""`account.created`: support made an account; the mail carries only the link to set a password (rule 6)."""

from __future__ import annotations

import pytest

from app.core.email import ACCOUNT_CREATED, TEMPLATES, Mail, OutboxMailer, account_created_mail

pytestmark = pytest.mark.anyio

TO = "zz-new@example.test"
LINK = "https://app.test/reset-password?token=" + "a" * 56


def test_the_mail_says_the_link_lasts_one_hour() -> None:
    """Supabase's recovery token lasts an hour (otp_expiry); the template says so with `link_hours`."""
    assert account_created_mail(TO, link=LINK).params["link_hours"] == "1"


def test_the_template_is_known() -> None:
    assert ACCOUNT_CREATED == "account.created"
    assert ACCOUNT_CREATED in TEMPLATES


async def test_the_mail_carries_the_set_password_link_only() -> None:
    mail = account_created_mail(TO, link=LINK)
    assert (mail.to, mail.template, mail.params) == (TO, ACCOUNT_CREATED, {"link": LINK, "link_hours": "1"})
    outbox = OutboxMailer()
    await outbox.send(mail)
    assert outbox.outbox == [mail]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"link": "https://evil.example/reset-password?token=" + "a" * 56 + "@"},
        {"link": "https://app.test/invites/" + "a" * 56},
        {"link": LINK},
        {"link": LINK, "link_hours": "one hour"},
        {"link": LINK, "link_hours": "1", "full_name": "Dr New Expert"},
        {"link": LINK, "link_hours": "1", "password": "hunter22"},
    ],
)
async def test_the_outbox_refuses_anything_else(params: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        await OutboxMailer().send(Mail(TO, ACCOUNT_CREATED, params))
