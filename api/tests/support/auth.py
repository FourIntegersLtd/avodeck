"""Supabase Auth helpers for tests: an admin client for local Supabase Auth, TOTP codes and a browser.

Only tests use the service-role key here, to arrange users and read what Supabase Auth holds.
Nothing here prints a key, a password, a code or a token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from tests.support.seed import SeededSession

SESSION_COOKIE = "av_session"
CSRF_COOKIE = "av_csrf"
CSRF_HEADER = "x-csrf-token"
# Every browser request carries an Origin; the API now requires one on a change (ADR 0031).
APP_ORIGIN = "https://app.test"
PASSWORD = "Correct-horse-battery-9"


def totp(secret: str, at: float | None = None) -> str:
    """RFC 6238 code (SHA-1, 30 seconds, 6 digits), as an authenticator app shows it."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((time.time() if at is None else at) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f"{(struct.unpack('>I', digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def new_email() -> str:
    return f"t{uuid.uuid4().hex[:12]}@example.test"


@dataclass(frozen=True)
class AuthAdmin:
    """Local Supabase Auth's admin API (tests only)."""

    url: str
    service_role_key: str = ""

    def _client(self) -> httpx.Client:
        key = self.service_role_key
        return httpx.Client(base_url=f"{self.url}/auth/v1", headers={"apikey": key, "authorization": f"Bearer {key}"})

    def create_user(self, email: str, password: str = PASSWORD) -> uuid.UUID:
        with self._client() as c:
            resp = c.post("/admin/users", json={"email": email, "password": password, "email_confirm": True})
        resp.raise_for_status()
        return uuid.UUID(resp.json()["id"])

    def user(self, user_id: uuid.UUID) -> dict[str, Any]:
        with self._client() as c:
            resp = c.get(f"/admin/users/{user_id}")
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        return body

    def verified_factors(self, user_id: uuid.UUID) -> list[str]:
        with self._client() as c:
            resp = c.get(f"/admin/users/{user_id}/factors")
        resp.raise_for_status()
        return [f["id"] for f in resp.json() if f["status"] == "verified"]

    def recovery_token(self, email: str) -> str:
        """The token the password-reset email would carry."""
        with self._client() as c:
            resp = c.post("/admin/generate_link", json={"type": "recovery", "email": email})
        resp.raise_for_status()
        token: str = resp.json()["hashed_token"]
        return token


async def add_csrf(request: httpx.Request) -> None:
    """What the web app does: copy the CSRF cookie into the header on every request."""
    cookie = request.headers.get("cookie", "")
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == CSRF_COOKIE and value:
            request.headers[CSRF_HEADER] = value


def cookie_header(session: SeededSession) -> str:
    return f"{SESSION_COOKIE}={session.token}; {CSRF_COOKIE}={session.csrf}"


@dataclass
class SignedIn:
    person_id: uuid.UUID
    email: str
    password: str
    secret: str
    recovery_codes: list[str]


async def sign_in_with_mfa(browser: httpx.AsyncClient, admin: AuthAdmin, email: str | None = None) -> SignedIn:
    """A new Supabase Auth user signs in through FastAPI, enrols an authenticator and verifies a code."""
    email = email or new_email()
    person = admin.create_user(email)
    resp = await browser.post("/v1/auth/sign-in", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    enrol = await browser.post("/v1/auth/mfa/enroll")
    assert enrol.status_code == 200, enrol.text
    secret = enrol.json()["secret"]
    verify = await browser.post("/v1/auth/mfa/verify", json={"code": totp(secret)})
    assert verify.status_code == 200, verify.text
    return SignedIn(person, email, PASSWORD, secret, verify.json()["recovery_codes"])
