"""Accounts Avodeck support creates for a new person (permissions.md "Avodeck staff"; migration 1012).

FastAPI makes the Supabase Auth login with no password, then the person and their own practice, and emails a
link to the web app's set-password page (it lasts 1 hour; support can send it again). Support never sees or
chooses the password and adds nobody to a team. Any failure after the login is made deletes it again; the
mail goes only after the request's transaction has committed. Ids and codes only in the audit trail and the
mails (rule 6).
"""

from __future__ import annotations

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
from app.features.organisations.schemas import SupportAccount, SupportAccountCreate
from app.features.organisations.service import refusal

TAKEN = ApiError("conflict", "An account with this email already exists.", 409)
REFUSALS = {
    "email_taken": TAKEN,
    "too_many_accounts": ApiError("too_many_attempts", "Too many attempts. Wait a while and try again.", 429),
    "sent_recently": ApiError("sent_recently", "The link was sent less than a minute ago. Wait a minute.", 429),
    "already_active": ApiError("conflict", "This person has already set up their account.", 409),
    "account_not_found": not_found(),
}
# Refusals that say something about an email; each one counts toward the staff member's 20 an hour.
REFUSED = "support.account_refused"
COUNTED = frozenset({"conflict", "invalid_email"})


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
    body: SupportAccountCreate, after_commit: BackgroundTasks,
) -> SupportAccount:  # fmt: skip
    try:
        await _db(repo.support_account_precheck(conn, body.email))
        person_id = await auth.admin_create_user(body.email)
    except ApiError as refused:
        if refused.code in COUNTED:  # kept though the request rolls back (audit.record_detached)
            audit.record_detached(action=REFUSED, target=f"refused:{refused.code}")
        raise
    try:
        # The set-password token first: if Auth cannot give one, nothing is written and the login goes.
        recovery = await auth.admin_recovery_token(body.email)
        account_id = await _db(repo.support_create_account(conn, person_id, body.email, body.full_name, region))
        found = next(a for a in await repo.support_accounts(conn) if a["id"] == account_id)
        await audit.record(conn, action="support.account_created", target=f"person:{person_id}")
    except BaseException:
        # Best effort. A failure of the commit itself (after this returns) cannot undo the login; support
        # then sees 409 for the email and the login is removed by hand (docs/ops/support-staff.md).
        await auth.admin_delete_user(person_id)
        raise
    after_commit.add_task(send_mail, mailer, account_created_mail(found["email"], link=_link(origin, recovery)))
    return SupportAccount(**found)


async def resend(
    conn: AsyncConnection, mailer: Mailer, auth: SupabaseAuth, origin: str, account_id: uuid.UUID,
    after_commit: BackgroundTasks,
) -> SupportAccount:  # fmt: skip
    found = await _db(repo.support_resend_account(conn, account_id))
    if found is None:
        raise not_found()
    recovery = await auth.admin_recovery_token(found["email"])
    await audit.record(conn, action="support.account_invite_resent", target=f"person:{found['person_id']}")
    after_commit.add_task(send_mail, mailer, account_created_mail(found["email"], link=_link(origin, recovery)))
    return SupportAccount(**found)
