"""Account (S28): signed-in devices and changing the sign-in email (architecture 5.2, 9).

An email change is 🔒 (step-up) and needs a link opened from both the old and the new address
within a day. Mail goes through the cell's mailer (a local stand-in until Resend).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Literal

import psycopg
from fastapi import Depends, Request, Response
from pydantic import BaseModel, Field

from app.core import audit
from app.core.auth import session as sessions
from app.core.auth.step_up import require_step_up
from app.core.auth.supabase import SupabaseAuth
from app.core.email import EMAIL_CHANGE_CONFIRM_NEW, EMAIL_CHANGE_CONFIRM_OLD, Mail, Mailer
from app.core.errors import ApiError
from app.core.http import CurrentSession, DbConn, public_router, router

account_router = router(prefix="/v1/me", tags=["account"])
account_public = public_router(prefix="/v1/me", tags=["account"])


class Device(BaseModel):
    id: uuid.UUID
    device_label: str
    created_at: datetime
    last_seen_at: datetime
    current: bool


class EmailChange(BaseModel):
    new_email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfirmationSent(BaseModel):
    status: Literal["confirmation_sent"]


class EmailLink(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class EmailChangeStatus(BaseModel):
    status: Literal["waiting_for_other_address", "email_changed"]


def invalid_link() -> ApiError:
    return ApiError("invalid_token", "That link has expired or was already used.", 400)


@account_router.get("/sessions", response_model=list[Device])
async def list_sessions(session: CurrentSession, conn: DbConn) -> list[Device]:
    cur = await conn.execute(
        "select id, device_label, created_at, last_seen_at from app.sessions"
        " where person_id = app.current_person_id() and revoked_at is null order by created_at, id"
    )
    return [
        Device(id=r[0], device_label=r[1], created_at=r[2], last_seen_at=r[3], current=r[0] == session.id)
        for r in await cur.fetchall()
    ]


@account_router.delete("/sessions/{session_id:uuid}", status_code=204)
async def end_session(session_id: uuid.UUID, conn: DbConn) -> Response:
    """Signs out one of the person's own devices; anyone else's is 404."""
    if await sessions.revoke(conn, "signed_out", session_id=session_id) == 0:
        raise ApiError("not_found", "Not found.", 404)
    await audit.record(conn, action="auth.session_revoked", target=f"session:{session_id}")
    return Response(status_code=204)


@account_router.post("/sessions/revoke-all", status_code=204, dependencies=[Depends(require_step_up)])
async def end_all_sessions(request: Request, session: CurrentSession, conn: DbConn) -> Response:
    """🔒 Signs out every device, this one included."""
    await sessions.revoke(conn, "signed_out_everywhere")
    await audit.record(conn, action="auth.sessions_revoked", target=f"person:{session.person_id}")
    if session.auth_access_token:
        auth: SupabaseAuth = request.app.state.auth
        await auth.sign_out(session.auth_access_token, scope="global")
    response = Response(status_code=204)
    sessions.clear_cookies(response)
    return response


@account_router.post(
    "/email-change", response_model=ConfirmationSent, status_code=202, dependencies=[Depends(require_step_up)]
)
async def request_email_change(
    body: EmailChange, request: Request, session: CurrentSession, conn: DbConn
) -> ConfirmationSent:
    """🔒 Sends a link to the old and the new address; both must be opened within a day."""
    old_token, new_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await conn.execute(
        "update app.email_changes set cancelled_at = now()"
        " where person_id = app.current_person_id() and completed_at is null and cancelled_at is null"
    )
    await conn.execute(
        "insert into app.email_changes (person_id, new_email, old_token_hash, new_token_hash)"
        " values (app.current_person_id(), %s, %s, %s)",
        (body.new_email.strip().lower(), sessions.digest(old_token), sessions.digest(new_token)),
    )
    await audit.record(conn, action="account.email_change_requested", target=f"person:{session.person_id}")
    base = f"{request.app.state.app_origin}/account/email/confirm?token="
    mailer: Mailer = request.app.state.mailer
    await mailer.send(Mail(to=session.email, template=EMAIL_CHANGE_CONFIRM_OLD, params={"link": base + old_token}))
    await mailer.send(
        Mail(to=body.new_email.strip().lower(), template=EMAIL_CHANGE_CONFIRM_NEW, params={"link": base + new_token})
    )
    return ConfirmationSent(status="confirmation_sent")


@account_public.post("/email-change/confirm", response_model=EmailChangeStatus)
async def confirm_email_change(body: EmailLink, request: Request) -> EmailChangeStatus:
    """Opened from either email. The second confirmation changes the sign-in address."""
    database = request.app.state.database
    auth: SupabaseAuth = request.app.state.auth
    try:
        async with database.anonymous() as conn:
            cur = await conn.execute(
                "select person_id, new_email, status from app.confirm_email_change(%s)",
                (sessions.digest(body.token),),
            )
            row = await cur.fetchone()
            if row is None:
                raise invalid_link()
            person_id, new_email, status = row
            if status == "completed":
                await auth.admin_set_email(person_id, new_email)  # failure rolls the change back
    except psycopg.errors.UniqueViolation as exc:
        raise ApiError("email_unavailable", "That email address cannot be used.", 409) from exc
    return EmailChangeStatus(status="email_changed" if status == "completed" else "waiting_for_other_address")
