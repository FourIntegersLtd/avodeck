"""Accounts Avodeck support creates for a new person (permissions.md "Avodeck staff"; migrations 1012, 1013).

FastAPI makes a temporary password (ADR 0024) and the Supabase Auth login with it, then the person (who must
change it: `people.must_change_password`) and their own practice. The password is returned once to the staff
member, who tells it to the person by phone or in person; it is never stored, logged, audited or emailed. When
the cell has a mail service, the person is also emailed a link to the web app's set-password page (1 hour).
Support adds nobody to a team. Any failure after the login is made deletes it again; the mail goes only after
the request's transaction has committed. Ids and codes only in the audit trail and the mails (rule 6).
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import psycopg
from fastapi import BackgroundTasks
from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import not_found
from app.core.auth.supabase import SupabaseAuth
from app.core.email import Mailer, account_created_mail
from app.core.errors import CONFLICT_SQLSTATE, ApiError
from app.core.notifications import send_mail
from app.features.organisations import repo
from app.features.organisations.schemas import SupportAccount, SupportAccountCreate, SupportAccountWithPassword
from app.features.organisations.service import refusal

TAKEN = ApiError("conflict", "An account with this email already exists.", 409)
REFUSALS = {
    "email_taken": TAKEN,
    "too_many_accounts": ApiError("too_many_attempts", "Too many attempts. Wait a while and try again.", 429),
    "sent_recently": ApiError("sent_recently", "A password was made less than a minute ago. Wait a minute.", 429),
    "already_active": ApiError("conflict", "This person has already set up their account.", 409),
    "account_not_found": not_found(),
}
# Refusals that say something about an email; each one counts toward the staff member's 20 an hour.
REFUSED = "support.account_refused"
COUNTED = frozenset({"conflict", "invalid_email"})

# No look-alikes (0/O, 1/l/I) so it can be read aloud or copied by hand.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
_LENGTH = 20


def temporary_password() -> str:
    """20 random characters (about 115 bits) in groups of four, with upper case, lower case and a digit."""
    while True:
        chars = "".join(secrets.choice(ALPHABET) for _ in range(_LENGTH))
        if any(c.isupper() for c in chars) and any(c.islower() for c in chars) and any(c.isdigit() for c in chars):
            return "-".join(chars[i : i + 4] for i in range(0, _LENGTH, 4))


def _mapped(exc: psycopg.Error) -> ApiError | None:
    if isinstance(exc, psycopg.errors.UniqueViolation):
        return TAKEN
    if exc.sqlstate == CONFLICT_SQLSTATE and (exc.diag.message_primary or "") in REFUSALS:
        return REFUSALS[exc.diag.message_primary or ""]
    return refusal(exc)


async def _db(call: Any) -> Any:
    try:
        return await call
    except psycopg.Error as exc:
        mapped = _mapped(exc)
        if mapped is None:
            raise
        raise mapped from None


def _link(origin: str, recovery: str) -> str:
    return f"{origin}/reset-password?token={recovery}"


async def accounts(conn: AsyncConnection) -> list[SupportAccount]:
    return [SupportAccount(**r) for r in await repo.support_accounts(conn)]


async def create(
    conn: AsyncConnection, mailer: Mailer, auth: SupabaseAuth, origin: str, region: str,
    body: SupportAccountCreate, after_commit: BackgroundTasks, *, mail: bool,
) -> SupportAccountWithPassword:  # fmt: skip
    password = temporary_password()
    try:
        await _db(repo.support_account_precheck(conn, body.email))
        person_id = await auth.admin_create_user(body.email, password=password)
    except ApiError as refused:
        if refused.code in COUNTED:  # kept though the request rolls back (audit.record_detached)
            audit.record_detached(action=REFUSED, target=f"refused:{refused.code}")
        raise
    try:
        # The set-password token first: if Auth cannot give one, nothing is written and the login goes.
        recovery = await auth.admin_recovery_token(body.email) if mail else None
        account_id = await _db(repo.support_create_account(conn, person_id, body.email, body.full_name, region))
        found = next(a for a in await repo.support_accounts(conn) if a["id"] == account_id)
        await audit.record(conn, action="support.account_created", target=f"person:{person_id}")
    except BaseException:
        # Best effort. A failure of the commit itself (after this returns) cannot undo the login; support
        # then sees 409 for the email and the login is removed by hand (docs/ops/support-staff.md).
        await auth.admin_delete_user(person_id)
        raise
    if recovery is not None:
        after_commit.add_task(send_mail, mailer, account_created_mail(found["email"], link=_link(origin, recovery)))
    return SupportAccountWithPassword(**found, temporary_password=password)


async def new_password(conn: AsyncConnection, auth: SupabaseAuth, account_id: uuid.UUID) -> SupportAccountWithPassword:
    """A new temporary password for an account still `invited`: the old one stops working, the person is signed
    out everywhere and must change it again (1013). Never once the person has a password of their own or has
    set up MFA (409; the database checks both, Auth is asked about MFA too). No email."""
    found = await _db(repo.support_new_password(conn, account_id))
    if found is None:
        raise not_found()
    if await auth.admin_has_mfa(found["person_id"]):  # set up though no session says so: theirs now
        raise REFUSALS["already_active"]
    password = temporary_password()
    await auth.admin_set_password(found["person_id"], password)
    await audit.record(conn, action="support.account_password_renewed", target=f"person:{found['person_id']}")
    return SupportAccountWithPassword(**found, temporary_password=password)
