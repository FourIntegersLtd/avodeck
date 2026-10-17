"""Sign-in, MFA and recovery codes through FastAPI against local Supabase Auth (architecture 5.2, 9, 10).

The browser only ever talks to FastAPI: FastAPI calls Supabase Auth, keeps its tokens server-side
and gives the browser an httpOnly, Secure, SameSite=Strict session cookie and a CSRF cookie.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

import httpx
import psycopg
import pytest
from fastapi.routing import APIRoute

from app.main import create_app
from tests.support.auth import CSRF_COOKIE, PASSWORD, SESSION_COOKIE, AuthAdmin, new_email, sign_in_with_mfa, totp
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

Browser = Callable[[], Awaitable[httpx.AsyncClient]]
JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")


def set_cookies(resp: httpx.Response) -> dict[str, str]:
    """Set-Cookie headers by cookie name, attributes lower-cased."""
    found = {}
    for header in resp.headers.get_list("set-cookie"):
        name = header.split("=", 1)[0]
        found[name] = header.lower()
    return found


def audit_rows(admin: psycopg.Connection, **where: object) -> list[tuple[object, ...]]:
    clause = " and ".join(f"{k} = %s" for k in where)
    return admin.execute(
        f"select person_id, action, target from app.audit_log where {clause} order by seq", tuple(where.values())
    ).fetchall()


# --- sign-in ------------------------------------------------------------------------------------


async def test_sign_in_sets_strict_httponly_secure_cookie_and_returns_no_token(
    browser: Browser, auth_admin: AuthAdmin
) -> None:
    email = new_email()
    auth_admin.create_user(email)
    client = await browser()
    resp = await client.post("/v1/auth/sign-in", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "mfa_required", "mfa_enrolled": False}

    cookies = set_cookies(resp)
    session = cookies[SESSION_COOKIE]
    assert "httponly" in session and "secure" in session and "samesite=strict" in session and "path=/" in session
    csrf = cookies[CSRF_COOKIE]
    assert "secure" in csrf and "samesite=strict" in csrf and "httponly" not in csrf

    assert not JWT.search(resp.text), "an Auth token must never reach the browser"
    assert "token" not in resp.text
    assert client.cookies[SESSION_COOKIE] not in resp.text
    assert not JWT.search(client.cookies[SESSION_COOKIE]), "the cookie holds an opaque session id, not a token"


async def test_sign_in_success_is_written_to_the_audit_log(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    email = new_email()
    person = auth_admin.create_user(email)
    resp = await (await browser()).post("/v1/auth/sign-in", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200
    assert [r[1] for r in audit_rows(admin, person_id=person, action="auth.sign_in")] == ["auth.sign_in"]


@pytest.mark.parametrize("known_email", [True, False])
async def test_failed_sign_in_is_refused_alike_and_audited_without_the_email(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection, known_email: bool
) -> None:
    email = new_email()
    if known_email:
        auth_admin.create_user(email)
    client = await browser()
    resp = await client.post("/v1/auth/sign-in", json={"email": email, "password": "Wrong-password-123"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"
    assert SESSION_COOKIE not in set_cookies(resp)

    target = "email:" + hashlib.sha256(email.encode()).hexdigest()
    assert audit_rows(admin, action="auth.sign_in_failed", target=target) == [(None, "auth.sign_in_failed", target)]


async def test_until_mfa_is_verified_the_session_reaches_only_the_mfa_routes(
    browser: Browser, auth_admin: AuthAdmin
) -> None:
    email = new_email()
    auth_admin.create_user(email)
    client = await browser()
    await client.post("/v1/auth/sign-in", json={"email": email, "password": PASSWORD})

    resp = await client.get("/v1/me")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "mfa_required"
    session = await client.get("/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["aal"] == "aal1" and session.json()["mfa_complete"] is False


# --- MFA ----------------------------------------------------------------------------------------


async def test_first_sign_in_enrols_mfa_and_issues_ten_recovery_codes(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    email = new_email()
    person = auth_admin.create_user(email)
    client = await browser()
    await client.post("/v1/auth/sign-in", json={"email": email, "password": PASSWORD})

    enrol = await client.post("/v1/auth/mfa/enroll")
    assert enrol.status_code == 200, enrol.text
    assert {"qr_code", "secret", "uri"} <= set(enrol.json())
    assert "no-store" in enrol.headers.get("cache-control", "")
    secret = enrol.json()["secret"]

    wrong = await client.post("/v1/auth/mfa/verify", json={"code": "000000" if totp(secret) != "000000" else "111111"})
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "invalid_code"
    assert (await client.get("/v1/me")).status_code == 403
    assert [r[1] for r in audit_rows(admin, person_id=person, action="auth.mfa_failed")] == ["auth.mfa_failed"]

    ok = await client.post("/v1/auth/mfa/verify", json={"code": totp(secret)})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "signed_in"
    codes = body["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert "no-store" in ok.headers.get("cache-control", "")
    stored = admin.execute(
        "select code_hash from app.mfa_recovery_codes where person_id = %s and used_at is null", (person,)
    ).fetchall()
    assert len(stored) == 10 and not ({r[0] for r in stored} & set(codes)), "codes are stored scrambled"

    me = await client.get("/v1/me")
    assert me.status_code == 200, me.text
    assert me.json()["mfa_enrolled"] is True
    assert auth_admin.verified_factors(person)
    session = (await client.get("/v1/auth/session")).json()
    assert session["aal"] == "aal2" and session["mfa_complete"] is True and session["step_up_until"]


async def test_mfa_verification_gives_a_new_session_cookie(browser: Browser, auth_admin: AuthAdmin) -> None:
    email = new_email()
    auth_admin.create_user(email)
    client = await browser()
    await client.post("/v1/auth/sign-in", json={"email": email, "password": PASSWORD})
    before = client.cookies[SESSION_COOKIE]
    secret = (await client.post("/v1/auth/mfa/enroll")).json()["secret"]
    await client.post("/v1/auth/mfa/verify", json={"code": totp(secret)})
    assert client.cookies[SESSION_COOKIE] != before

    stale = await browser()
    stale.cookies.set(SESSION_COOKIE, before)
    assert (await stale.get("/v1/auth/session")).status_code == 401


async def test_later_sign_ins_ask_for_the_code_and_issue_no_new_recovery_codes(
    browser: Browser, auth_admin: AuthAdmin
) -> None:
    first = await sign_in_with_mfa(await browser(), auth_admin)
    client = await browser()
    resp = await client.post("/v1/auth/sign-in", json={"email": first.email, "password": PASSWORD})
    assert resp.json() == {"status": "mfa_required", "mfa_enrolled": True}
    assert (await client.post("/v1/auth/mfa/enroll")).json()["error"]["code"] == "already_enrolled"
    # The next code: the one used to sign in first cannot be used again (RFC 6238 section 5.2).
    ok = await client.post("/v1/auth/mfa/verify", json={"code": totp(first.secret, at=time.time() + 30)})
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"status": "signed_in", "recovery_codes": None}
    assert (await client.get("/v1/me")).status_code == 200


# --- recovery codes -----------------------------------------------------------------------------


async def test_a_recovery_code_works_once_and_asks_to_enrol_again(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    signed = await sign_in_with_mfa(await browser(), auth_admin)
    code = signed.recovery_codes[0]
    client = await browser()
    body = {"email": signed.email, "password": PASSWORD, "code": code}

    resp = await client.post("/v1/auth/mfa/recover", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "mfa_required", "mfa_enrolled": False}
    assert auth_admin.verified_factors(signed.person_id) == []
    assert (await client.get("/v1/me")).json()["error"]["code"] == "mfa_required"
    assert [r[1] for r in audit_rows(admin, person_id=signed.person_id, action="auth.recovery_code_used")] == [
        "auth.recovery_code_used"
    ]

    again = await (await browser()).post("/v1/auth/mfa/recover", json=body)
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "invalid_code"

    secret = (await client.post("/v1/auth/mfa/enroll")).json()["secret"]
    verified = await client.post("/v1/auth/mfa/verify", json={"code": totp(secret)})
    assert len(verified.json()["recovery_codes"]) == 10
    old = await (await browser()).post("/v1/auth/mfa/recover", json={**body, "code": signed.recovery_codes[1]})
    assert old.status_code == 401, "re-enrolling replaces the old set"


async def test_recovery_needs_the_password_too(browser: Browser, auth_admin: AuthAdmin) -> None:
    signed = await sign_in_with_mfa(await browser(), auth_admin)
    resp = await (await browser()).post(
        "/v1/auth/mfa/recover",
        json={"email": signed.email, "password": "Wrong-password-123", "code": signed.recovery_codes[0]},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_regenerating_recovery_codes_invalidates_the_old_set(browser: Browser, auth_admin: AuthAdmin) -> None:
    client = await browser()
    signed = await sign_in_with_mfa(client, auth_admin)
    resp = await client.post("/v1/auth/mfa/recovery-codes")
    assert resp.status_code == 200, resp.text
    new_codes = resp.json()["recovery_codes"]
    assert len(new_codes) == 10 and not set(new_codes) & set(signed.recovery_codes)
    assert "no-store" in resp.headers.get("cache-control", "")

    body = {"email": signed.email, "password": PASSWORD}
    old = await (await browser()).post("/v1/auth/mfa/recover", json={**body, "code": signed.recovery_codes[0]})
    assert old.status_code == 401
    new = await (await browser()).post("/v1/auth/mfa/recover", json={**body, "code": new_codes[0]})
    assert new.status_code == 200, new.text


async def test_an_organisation_owner_cannot_reset_another_persons_mfa(
    browser: Browser, auth_admin: AuthAdmin, seed: Seed, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    member = await sign_in_with_mfa(await browser(), auth_admin)
    owner = seed.person()
    seed.org(kind="team", members={owner: "owner", member.person_id: "expert"})

    paths = [r.path for r in create_app().routes if isinstance(r, APIRoute)]
    assert not [p for p in paths if ("/auth" in p or "/me" in p) and "{person_id}" in p]

    owner_client = await client_for(owner)
    resp = await owner_client.post("/v1/auth/mfa/recovery-codes", json={"person_id": str(member.person_id)})
    assert resp.status_code in (200, 422)
    unused = admin.execute(
        "select count(*) from app.mfa_recovery_codes where person_id = %s and used_at is null", (member.person_id,)
    ).fetchone()
    assert unused == (10,)
    assert auth_admin.verified_factors(member.person_id)


# --- sign-out and logs --------------------------------------------------------------------------


async def test_sign_out_ends_the_session_and_clears_the_cookies(browser: Browser, auth_admin: AuthAdmin) -> None:
    client = await browser()
    await sign_in_with_mfa(client, auth_admin)
    token = client.cookies[SESSION_COOKIE]
    resp = await client.post("/v1/auth/sign-out")
    assert resp.status_code == 204
    cleared = set_cookies(resp)
    assert "max-age=0" in cleared[SESSION_COOKIE] and "max-age=0" in cleared[CSRF_COOKIE]

    replay = await browser()
    replay.cookies.set(SESSION_COOKIE, token)
    assert (await replay.get("/v1/me")).status_code == 401


async def test_signed_out_requests_get_401(browser: Browser) -> None:
    client = await browser()
    client.cookies.set(SESSION_COOKIE, "made-up")
    resp = await client.get("/v1/auth/session")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_signed_in"


async def test_no_email_password_code_or_token_reaches_the_logs(
    browser: Browser, auth_admin: AuthAdmin, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = await browser()
    signed = await sign_in_with_mfa(client, auth_admin)
    await client.post("/v1/auth/mfa/recovery-codes")
    await (await browser()).post(
        "/v1/auth/mfa/recover", json={"email": signed.email, "password": PASSWORD, "code": signed.recovery_codes[0]}
    )
    await (await browser()).post("/v1/auth/sign-in", json={"email": signed.email, "password": "Wrong-password-123"})
    await (await browser()).post("/v1/auth/password/forgot", json={"email": signed.email})

    logged = caplog.text
    secrets_seen = [signed.email, signed.email.split("@")[0], PASSWORD, signed.secret, *signed.recovery_codes]
    secrets_seen.append(client.cookies.get(SESSION_COOKIE) or "")
    assert not [s for s in secrets_seen if s and s in logged]
    assert not JWT.search(logged)
    assert uuid.UUID(str(signed.person_id))
