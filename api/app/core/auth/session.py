"""Device sessions (architecture 9, Login).

The browser holds two cookies: `av_session`, a random token (httpOnly, Secure, SameSite=Strict),
and `av_csrf`, a second random token the web app copies into the `X-CSRF-Token` header on every
state-changing request (double submit). The database keeps only the SHA-256 of each. A session
ends after 30 minutes without activity or 12 hours after sign-in. MFA completed in the last
10 minutes (`aal2`) is the step-up that risky actions need (rule 10).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from psycopg import AsyncConnection

from app.core.auth.supabase import AuthTokens
from app.core.config import local_mfa_skip
from app.core.crypto import CiphertextInvalid, cell_secret_box
from app.core.db import Database
from app.core.errors import ApiError

SESSION_COOKIE = "av_session"
CSRF_COOKIE = "av_csrf"
CSRF_HEADER = "x-csrf-token"
IDLE_TIMEOUT = timedelta(minutes=30)
MAX_AGE = timedelta(hours=12)
STEP_UP_WINDOW = timedelta(minutes=10)
DEVICE_LABEL_MAX = 120


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def email_target(email: str) -> str:
    """How an audit row names an address it may not store."""
    return "email:" + digest(email.strip().lower())


def not_signed_in() -> ApiError:
    return ApiError("not_signed_in", "Please sign in.", 401)


@dataclass(frozen=True)
class Session:
    id: uuid.UUID
    person_id: uuid.UUID
    email: str = field(repr=False)
    full_name: str | None = field(repr=False)
    csrf_hash: str = field(repr=False)
    aal: str
    mfa_enrolled: bool
    mfa_verified_at: datetime | None
    last_seen_at: datetime
    auth_access_token: str | None = field(repr=False)
    auth_refresh_token: str | None = field(repr=False)
    auth_expires_at: datetime | None

    @property
    def mfa_complete(self) -> bool:
        return self.aal == "aal2" and self.mfa_enrolled

    @property
    def step_up_until(self) -> datetime | None:
        if not self.mfa_complete or self.mfa_verified_at is None:
            return None
        return self.mfa_verified_at + STEP_UP_WINDOW

    @property
    def stepped_up(self) -> bool:
        until = self.step_up_until
        return until is not None and until > datetime.now(UTC)

    def csrf_matches(self, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(digest(token or ""), self.csrf_hash)


async def resolve(database: Database, token: str | None, *, touch: bool) -> Session:
    """The session behind the cookie; 401 `not_signed_in` or `session_expired` otherwise."""
    if not token:
        raise not_signed_in()
    # One round trip: the lookup (and its touch, inside the function) in its own anonymous transaction.
    row = await database.anonymous_one(
        "select * from app.session_resolve(%s, %s, %s, %s)", (digest(token), IDLE_TIMEOUT, MAX_AGE, touch)
    )
    if row is None:
        raise not_signed_in()
    *fields, state = row
    if state == "expired":
        raise ApiError("session_expired", "Your session has ended. Please sign in again.", 401)
    if state != "active":
        raise not_signed_in()
    session = Session(*fields)
    if local_mfa_skip():
        session = replace(session, aal="aal2", mfa_enrolled=True, mfa_verified_at=datetime.now(UTC))
    return replace(
        session,
        auth_access_token=_open(session.auth_access_token, "access", session.person_id),
        auth_refresh_token=_open(session.auth_refresh_token, "refresh", session.person_id),
    )


def _seal(token: str | None, kind: str, person_id: uuid.UUID) -> str | None:
    """Supabase Auth tokens are stored sealed (core/crypto.py), bound to their kind and person."""
    if token is None:
        return None
    return cell_secret_box().seal(token, purpose=f"session.auth_{kind}", subject=str(person_id))


def _open(sealed: str | None, kind: str, person_id: uuid.UUID) -> str | None:
    if sealed is None:
        return None
    try:
        return cell_secret_box().open(sealed, purpose=f"session.auth_{kind}", subject=str(person_id))
    except CiphertextInvalid:
        return None  # sealed under another key: the person signs in again when it is needed


def current_session(request: Request) -> Session:
    session = getattr(request.state, "session", None)
    if not isinstance(session, Session):
        raise RuntimeError("the session is only available on routes built with app.core.http.router()")
    return session


def device_label(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:DEVICE_LABEL_MAX]


@dataclass(frozen=True)
class NewTokens:
    session: str = field(repr=False)
    csrf: str = field(repr=False)


def new_tokens() -> NewTokens:
    return NewTokens(secrets.token_urlsafe(32), secrets.token_urlsafe(32))


async def open_session(conn: AsyncConnection, auth: AuthTokens, label: str) -> tuple[uuid.UUID, NewTokens]:
    """A new aal1 session after the password step. Runs as the person (RLS)."""
    tokens = new_tokens()
    cur = await conn.execute(
        """
        insert into app.sessions (person_id, email, token_hash, csrf_hash, device_label, aal, mfa_enrolled,
                                  auth_session_id, auth_access_token, auth_refresh_token, auth_expires_at)
        values (%s, %s, %s, %s, %s, 'aal1', %s, %s, %s, %s, %s) returning id
        """,
        (
            auth.person_id,
            auth.email,
            digest(tokens.session),
            digest(tokens.csrf),
            label,
            auth.mfa_enrolled,
            auth.session_id,
            _seal(auth.access_token, "access", auth.person_id),
            _seal(auth.refresh_token, "refresh", auth.person_id),
            auth.expires_at,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0], tokens


async def store_auth_tokens(conn: AsyncConnection, session_id: uuid.UUID, auth: AuthTokens) -> None:
    await conn.execute(
        "update app.sessions set auth_session_id = %s, auth_access_token = %s, auth_refresh_token = %s,"
        " auth_expires_at = %s where id = %s",
        (
            auth.session_id,
            _seal(auth.access_token, "access", auth.person_id),
            _seal(auth.refresh_token, "refresh", auth.person_id),
            auth.expires_at,
            session_id,
        ),
    )


async def revoke(conn: AsyncConnection, reason: str, *, session_id: uuid.UUID | None = None) -> int:
    """Ends one of the person's sessions, or all of them. Returns how many ended."""
    query = "update app.sessions set revoked_at = now(), revoked_reason = %s where revoked_at is null"
    params: tuple[object, ...] = (reason,)
    if session_id is not None:
        query += " and id = %s"
        params += (session_id,)
    cur = await conn.execute(query, params)
    return cur.rowcount


def _cookie(response: Response, name: str, value: str, *, http_only: bool) -> None:
    response.set_cookie(
        name,
        value,
        max_age=int(MAX_AGE.total_seconds()),
        path="/",
        secure=True,
        httponly=http_only,
        samesite="strict",
    )


def set_cookies(response: Response, tokens: NewTokens) -> None:
    _cookie(response, SESSION_COOKIE, tokens.session, http_only=True)
    _cookie(response, CSRF_COOKIE, tokens.csrf, http_only=False)
    response.headers["cache-control"] = "no-store"


def clear_cookies(response: Response) -> None:
    for name, http_only in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(name, path="/", secure=True, httponly=http_only, samesite="strict")
