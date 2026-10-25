"""Sign-in, MFA, recovery codes, sign-out, password change and reset (architecture 5.2, 9 "Login"; S01).

FastAPI is the only caller of Supabase Auth (rule 4). The browser gets an opaque session cookie
and a CSRF cookie, never an Auth token. Every sign-in needs the password and then a code from an
authenticator app (enrolled on first sign-in); ten one-use recovery codes cover a lost phone.
Nothing here logs an address, password, code or token; audit rows name a failed address only by
its hash.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Depends, Request, Response
from pydantic import BaseModel, Field

from app.core import audit
from app.core.auth import session as sessions
from app.core.auth.step_up import require_step_up
from app.core.auth.supabase import AuthError, AuthTokens, SupabaseAuth
from app.core.config import local_mfa_skip
from app.core.db import Database, RequestScope
from app.core.email import RECOVERY_CODE_USED, Mail, Mailer
from app.core.errors import ApiError
from app.core.http import CurrentSession, DbConn, public_router, router

auth_public = public_router(prefix="/v1/auth", tags=["auth"])
auth_pending = router(prefix="/v1/auth", tags=["auth"], mfa_pending=True)
auth_status = router(prefix="/v1/auth", tags=["auth"], mfa_pending=True, touch=False)
auth_signed_in = router(prefix="/v1/auth", tags=["auth"])
# Changing the password: also reachable while the person is still on a temporary password (ADR 0024).
auth_password = router(prefix="/v1/auth", tags=["auth"], password_pending=True)

RECOVERY_CODE_COUNT = 10
_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PASSWORD_MIN = 12


# --- helpers ------------------------------------------------------------------------------------


def _auth(request: Request) -> SupabaseAuth:
    auth: SupabaseAuth = request.app.state.auth
    return auth


def _database(request: Request) -> Database:
    database: Database | None = request.app.state.database
    if database is None:
        raise ApiError("unavailable", "The service is not ready.", 503)
    return database


def _mailer(request: Request) -> Mailer:
    mailer: Mailer = request.app.state.mailer
    return mailer


def scope(person_id: uuid.UUID) -> RequestScope:
    return RequestScope(person_id=person_id, org_ids=(), request_id=uuid.uuid4().hex)


async def audit_apart(database: Database, person_id: uuid.UUID, action: str, target: str) -> None:
    """An audit row in its own transaction, so it survives the refused request's rollback."""
    async with database.request(scope(person_id)) as conn:
        await audit.record(conn, action=action, target=target)


MFA_MAX_FAILURES = 5
MFA_WINDOW = timedelta(minutes=15)
MFA_CODE_LIFE = timedelta(seconds=90)  # a code stays valid for its 30 seconds plus one step either side


async def _mfa_failed(database: Database, person_id: uuid.UUID, target: str) -> None:
    """Counts a wrong code (a new window after 15 minutes) and audits it, apart from the refused request."""
    async with database.request(scope(person_id)) as conn:
        await conn.execute(
            "insert into app.mfa_attempts (person_id, failures) values (app.current_person_id(), 1)"
            " on conflict (person_id) do update set"
            " failures = case when app.mfa_attempts.window_started_at > now() - %s"
            "   then app.mfa_attempts.failures + 1 else 1 end,"
            " window_started_at = case when app.mfa_attempts.window_started_at > now() - %s"
            "   then app.mfa_attempts.window_started_at else now() end",
            (MFA_WINDOW, MFA_WINDOW),
        )
        await audit.record(conn, action="auth.mfa_failed", target=target)


PASSWORD_MAX_FAILURES = 5
PASSWORD_WINDOW = timedelta(minutes=15)


async def _password_change_failed(database: Database, person_id: uuid.UUID) -> None:
    """Counts a wrong current password (a new window after 15 minutes) and audits it, apart from the refused
    request, so its rollback keeps the count (migration 0036)."""
    async with database.request(scope(person_id)) as conn:
        await conn.execute(
            "insert into app.password_change_attempts (person_id, failures) values (app.current_person_id(), 1)"
            " on conflict (person_id) do update set"
            " failures = case when app.password_change_attempts.window_started_at > now() - %s"
            "   then app.password_change_attempts.failures + 1 else 1 end,"
            " window_started_at = case when app.password_change_attempts.window_started_at > now() - %s"
            "   then app.password_change_attempts.window_started_at else now() end",
            (PASSWORD_WINDOW, PASSWORD_WINDOW),
        )
        await audit.record(conn, action="auth.password_change_failed", target=f"person:{person_id}")


async def audit_anonymous(database: Database, action: str, email: str) -> None:
    async with database.anonymous() as conn:
        await conn.execute("select app.audit_anonymous(%s, %s)", (action, sessions.email_target(email)))


def _normalise_code(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def code_hash(person_id: uuid.UUID, code: str) -> str:
    return sessions.digest(f"{person_id}:{_normalise_code(code)}")


def _new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


async def issue_recovery_codes(conn: DbConn, person_id: uuid.UUID) -> list[str]:
    """Ten new one-use codes; every older code stops working."""
    await conn.execute("delete from app.mfa_recovery_codes where person_id = app.current_person_id()")
    codes = [_new_code() for _ in range(RECOVERY_CODE_COUNT)]
    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into app.mfa_recovery_codes (person_id, code_hash) values (app.current_person_id(), %s)",
            [(code_hash(person_id, c),) for c in codes],
        )
    return codes


async def fresh_access_token(conn: DbConn, session: sessions.Session, auth: SupabaseAuth) -> str:
    """The session's Supabase Auth access token, refreshed when it is about to expire."""
    if not session.auth_access_token or not session.auth_refresh_token:
        raise ApiError("session_expired", "Your session has ended. Please sign in again.", 401)
    expires = session.auth_expires_at
    if expires is not None and expires > datetime.now(UTC) + timedelta(seconds=60):
        return session.auth_access_token
    tokens = await auth.refresh(session.auth_refresh_token)
    await sessions.store_auth_tokens(conn, session.id, tokens)
    return tokens.access_token


async def _start_session(
    request: Request, response: Response, database: Database, tokens: AuthTokens, action: str
) -> None:
    async with database.request(scope(tokens.person_id)) as conn:
        await conn.execute("select app.ensure_person(%s, %s, %s)", (tokens.email, None, request.app.state.region))
        session_id, new = await sessions.open_session(conn, tokens, sessions.device_label(request))
        await audit.record(conn, action=action, target=f"session:{session_id}")
    sessions.set_cookies(response, new)


# --- models -------------------------------------------------------------------------------------


# No control characters: they cannot be part of an address, password, code or link token, and Auth
# would refuse them as an outage (found by Schemathesis).
PLAIN = r"^[^\x00-\x1f\x7f]*$"


class SignIn(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=PLAIN)
    password: str = Field(min_length=1, max_length=256, pattern=PLAIN)


class SignInResult(BaseModel):
    status: Literal["mfa_required", "signed_in"]
    mfa_enrolled: bool


class Enrolment(BaseModel):
    qr_code: str
    secret: str
    uri: str


class Code(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class Verified(BaseModel):
    status: Literal["signed_in"]
    recovery_codes: list[str] | None


class RecoveryCodes(BaseModel):
    recovery_codes: list[str]


class RecoveryCodeCount(BaseModel):
    remaining: int
    total: int


class Recover(SignIn):
    code: str = Field(min_length=6, max_length=32, pattern=PLAIN)


class SessionStatus(BaseModel):
    aal: Literal["aal1", "aal2"]
    mfa_enrolled: bool
    mfa_complete: bool
    step_up_until: datetime | None
    minutes_until_timeout: int
    must_change_password: bool = False  # on a temporary password from support: choose one before anything else


class Forgot(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=PLAIN)


class Sent(BaseModel):
    status: Literal["sent"]


class Reset(BaseModel):
    token: str = Field(min_length=1, max_length=512, pattern=PLAIN)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=256, pattern=PLAIN)


class PasswordChanged(BaseModel):
    status: Literal["password_changed"]


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=256, pattern=PLAIN)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=256, pattern=PLAIN)


# --- signing in ---------------------------------------------------------------------------------


@auth_public.post("/sign-in", response_model=SignInResult)
async def sign_in(body: SignIn, request: Request, response: Response) -> SignInResult:
    """Password step. Sets the session cookies; the session stays at `mfa_required` until a code is verified."""
    database = _database(request)
    try:
        tokens = await _auth(request).sign_in_with_password(body.email, body.password)
    except AuthError as exc:
        if exc.code == "invalid_credentials":
            await audit_anonymous(database, "auth.sign_in_failed", body.email)
        raise
    await _start_session(request, response, database, tokens, "auth.sign_in")
    status: Literal["mfa_required", "signed_in"] = "signed_in" if local_mfa_skip() else "mfa_required"
    return SignInResult(status=status, mfa_enrolled=tokens.mfa_enrolled)


@auth_pending.post("/mfa/enroll", response_model=Enrolment)
async def enroll(request: Request, response: Response, session: CurrentSession, conn: DbConn) -> Enrolment:
    """First sign-in: a new authenticator app (QR code and the same secret as text)."""
    if session.mfa_enrolled:
        raise ApiError("already_enrolled", "An authenticator app is already set up.", 409)
    auth = _auth(request)
    enrolment = await auth.enroll_totp(await fresh_access_token(conn, session, auth))
    response.headers["cache-control"] = "no-store"
    return Enrolment(qr_code=enrolment["qr_code"], secret=enrolment["secret"], uri=enrolment["uri"])


@auth_pending.post("/mfa/verify", response_model=Verified)
async def verify(body: Code, request: Request, response: Response, session: CurrentSession, conn: DbConn) -> Verified:
    """A code from the authenticator app: completes sign-in, or steps up for 10 minutes.

    The session gets new cookies. Finishing enrolment issues the ten recovery codes, shown once.
    """
    auth = _auth(request)
    access = await fresh_access_token(conn, session, auth)
    wanted = "verified" if session.mfa_enrolled else "unverified"
    factors = sorted(
        (f for f in await auth.factors(access) if f.get("status") == wanted),
        key=lambda f: str(f.get("created_at", "")),
    )
    if not factors:
        raise ApiError("not_enrolled", "Set up your authenticator app first.", 409)
    database, digest = _database(request), code_hash(session.person_id, body.code)
    cur = await conn.execute(
        "select failures >= %s and window_started_at > now() - %s, last_code_hash = %s and last_code_at > now() - %s"
        " from app.mfa_attempts where person_id = app.current_person_id()",
        (MFA_MAX_FAILURES, MFA_WINDOW, digest, MFA_CODE_LIFE),
    )
    row = await cur.fetchone()
    locked, reused = (bool(row[0]), bool(row[1])) if row else (False, False)
    if locked:
        raise ApiError("too_many_attempts", "Too many codes tried. Wait 15 minutes and try again.", 429)
    try:
        if reused:
            raise AuthError("invalid_code", "That code did not work. Wait for the next code and try again.", 400)
        tokens = await auth.verify_totp(access, str(factors[-1]["id"]), body.code)
    except AuthError as exc:
        if exc.code == "invalid_code":
            await _mfa_failed(database, session.person_id, f"session:{session.id}")
        raise
    if tokens.aal != "aal2":
        raise ApiError("invalid_code", "That code did not work.", 400)
    new = sessions.new_tokens()
    await conn.execute(
        "update app.sessions set token_hash = %s, csrf_hash = %s, aal = 'aal2', mfa_enrolled = true,"
        " mfa_verified_at = now(), last_seen_at = now() where id = %s",
        (sessions.digest(new.session), sessions.digest(new.csrf), session.id),
    )
    await sessions.store_auth_tokens(conn, session.id, tokens)
    await conn.execute(
        "insert into app.mfa_attempts (person_id, last_code_hash, last_code_at)"
        " values (app.current_person_id(), %s, now()) on conflict (person_id) do update"
        " set failures = 0, window_started_at = now(), last_code_hash = excluded.last_code_hash,"
        " last_code_at = excluded.last_code_at",
        (digest,),
    )
    codes = None if session.mfa_enrolled else await issue_recovery_codes(conn, session.person_id)
    await audit.record(conn, action="auth.mfa_verified", target=f"session:{session.id}")
    sessions.set_cookies(response, new)
    return Verified(status="signed_in", recovery_codes=codes)


@auth_signed_in.get("/mfa/recovery-codes", response_model=RecoveryCodeCount)
async def count_recovery_codes(response: Response, session: CurrentSession, conn: DbConn) -> RecoveryCodeCount:
    """How many backup codes are still unused (S28 "7 of 10 left"); never the codes."""
    response.headers["Cache-Control"] = "no-store"
    async with conn.cursor() as cur:
        await cur.execute(
            "select count(*) from app.mfa_recovery_codes where person_id = app.current_person_id() and used_at is null"
        )
        row = await cur.fetchone()
    return RecoveryCodeCount(remaining=int(row[0]) if row else 0, total=RECOVERY_CODE_COUNT)


@auth_signed_in.post("/mfa/recovery-codes", response_model=RecoveryCodes, dependencies=[Depends(require_step_up)])
async def regenerate_recovery_codes(response: Response, session: CurrentSession, conn: DbConn) -> RecoveryCodes:
    """🔒 Ten new recovery codes for the signed-in person only; the old set stops working."""
    codes = await issue_recovery_codes(conn, session.person_id)
    await audit.record(conn, action="auth.recovery_codes_issued", target=f"person:{session.person_id}")
    response.headers["cache-control"] = "no-store"
    return RecoveryCodes(recovery_codes=codes)


@auth_public.post("/mfa/recover", response_model=SignInResult)
async def recover(body: Recover, request: Request, response: Response) -> SignInResult:
    """Lost phone: password plus one recovery code. The code is used up, the old authenticator is
    removed and the new session must enrol a new one (which issues a new set of codes)."""
    database, auth = _database(request), _auth(request)
    try:
        tokens = await auth.sign_in_with_password(body.email, body.password)
    except AuthError as exc:
        if exc.code == "invalid_credentials":
            await audit_anonymous(database, "auth.sign_in_failed", body.email)
        raise
    person = tokens.person_id
    async with database.request(scope(person)) as conn:
        cur = await conn.execute(
            "update app.mfa_recovery_codes set used_at = now()"
            " where person_id = app.current_person_id() and code_hash = %s and used_at is null returning id",
            (code_hash(person, body.code),),
        )
        used = await cur.fetchone()
        if used is not None:
            await auth.admin_delete_totp_factors(person)
            await sessions.revoke(conn, "recovery")  # whoever has the lost phone's sessions is signed out
            await audit.record(conn, action="auth.recovery_code_used", target=f"person:{person}")
    if used is None:
        await audit_apart(database, person, "auth.recovery_failed", f"person:{person}")
        await auth.sign_out(tokens.access_token)
        raise ApiError("invalid_code", "That recovery code did not work.", 401)
    await auth.sign_out(tokens.access_token, scope="global")
    fresh = await auth.sign_in_with_password(body.email, body.password)
    await _start_session(request, response, database, fresh, "auth.sign_in")
    await _mailer(request).send(Mail(to=fresh.email, template=RECOVERY_CODE_USED))
    return SignInResult(status="mfa_required", mfa_enrolled=False)


@auth_pending.post("/sign-out", status_code=204)
async def sign_out(request: Request, session: CurrentSession, conn: DbConn) -> Response:
    await sessions.revoke(conn, "signed_out", session_id=session.id)
    await audit.record(conn, action="auth.sign_out", target=f"session:{session.id}")
    if session.auth_access_token:
        await _auth(request).sign_out(session.auth_access_token)
    response = Response(status_code=204)
    sessions.clear_cookies(response)
    return response


@auth_status.get("/session", response_model=SessionStatus)
async def session_status(session: CurrentSession) -> SessionStatus:
    """Where this session stands; reading it does not count as activity."""
    left = session.last_seen_at + sessions.IDLE_TIMEOUT - datetime.now(UTC)
    return SessionStatus(
        aal="aal2" if session.aal == "aal2" else "aal1",
        mfa_enrolled=session.mfa_enrolled,
        mfa_complete=session.mfa_complete,
        step_up_until=session.step_up_until if session.stepped_up else None,
        minutes_until_timeout=max(0, int(left.total_seconds() // 60)),
        must_change_password=session.must_change_password,
    )


# --- password reset -----------------------------------------------------------------------------


@auth_public.post("/password/forgot", response_model=Sent, status_code=202)
async def forgot_password(body: Forgot, request: Request) -> Sent:
    """The same answer whether or not the address has an account."""
    try:
        await _auth(request).send_password_reset(body.email)
    except AuthError:
        pass
    await audit_anonymous(_database(request), "auth.password_reset_requested", body.email)
    return Sent(status="sent")


@auth_public.post("/password/reset", response_model=PasswordChanged)
async def reset_password(body: Reset, request: Request) -> PasswordChanged:
    """The link from the reset email plus a new password. Signs out every device."""
    database, auth = _database(request), _auth(request)
    tokens = await auth.verify_recovery(body.token)
    await auth.admin_set_password(tokens.person_id, body.new_password)
    await auth.sign_out(tokens.access_token, scope="global")
    async with database.request(scope(tokens.person_id)) as conn:
        await conn.execute("select app.password_changed()")  # a temporary password is replaced too
        await sessions.revoke(conn, "password_reset")
        await audit.record(conn, action="auth.password_reset", target=f"person:{tokens.person_id}")
    return PasswordChanged(status="password_changed")


@auth_password.post("/password/change", response_model=PasswordChanged)
async def change_password(
    body: ChangePassword, request: Request, response: Response, session: CurrentSession, conn: DbConn
) -> PasswordChanged:
    """The signed-in person's current password and a new one (ADR 0024). Clears a temporary password's
    block and signs out the person's other devices; this one stays signed in."""
    if body.new_password == body.current_password:
        raise ApiError("same_password", "Choose a password different from the one you have now.", 422)
    cur = await conn.execute(
        "select failures >= %s and window_started_at > now() - %s"
        " from app.password_change_attempts where person_id = app.current_person_id()",
        (PASSWORD_MAX_FAILURES, PASSWORD_WINDOW),
    )
    row = await cur.fetchone()
    if row and row[0]:
        raise ApiError("too_many_attempts", "Too many wrong passwords. Wait 15 minutes and try again.", 429)
    auth = _auth(request)
    try:
        check = await auth.sign_in_with_password(session.email, body.current_password)
    except AuthError as exc:
        if exc.code != "invalid_credentials":
            raise
        await _password_change_failed(_database(request), session.person_id)
        raise ApiError("wrong_password", "That is not your current password.", 400) from None
    await auth.sign_out(check.access_token)
    await auth.admin_set_password(session.person_id, body.new_password)
    await conn.execute("select app.password_changed()")
    await sessions.revoke(conn, "password_changed", keep=session.id)
    await conn.execute(
        "update app.password_change_attempts set failures = 0, window_started_at = now()"
        " where person_id = app.current_person_id()"
    )
    await audit.record(conn, action="auth.password_changed", target=f"person:{session.person_id}")
    response.headers["Cache-Control"] = "no-store"
    return PasswordChanged(status="password_changed")
