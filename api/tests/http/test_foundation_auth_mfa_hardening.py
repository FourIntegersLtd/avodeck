"""MFA codes and recovery against local Supabase Auth (architecture 9; RFC 6238 section 5.2).

- A code that worked cannot be used again (a shoulder-surfed or intercepted code is spent).
- After five wrong codes in 15 minutes the person is locked out of code entry for the rest of the
  window (429 `too_many_attempts`), on every device, even with the right code.
- Using a recovery code signs out every other device: whoever had the lost phone's sessions is out.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import httpx
import psycopg
import pytest

from tests.support.auth import PASSWORD, AuthAdmin, sign_in_with_mfa, totp

pytestmark = pytest.mark.anyio

Browser = Callable[[], Awaitable[httpx.AsyncClient]]


def wrong(secret: str) -> str:
    near = {totp(secret, at=time.time() + d) for d in (-60, -30, 0, 30, 60)}
    return next(c for c in ("000000", "111111", "222222", "333333", "444444", "555555", "666666") if c not in near)


async def signed_in_again(browser: Browser, email: str) -> httpx.AsyncClient:
    client = await browser()
    resp = await client.post("/v1/auth/sign-in", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return client


async def test_a_code_that_worked_cannot_be_used_again(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    first = await sign_in_with_mfa(await browser(), auth_admin)
    client = await signed_in_again(browser, first.email)
    reused = await client.post("/v1/auth/mfa/verify", json={"code": totp(first.secret)})
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "invalid_code"
    assert (await client.get("/v1/me")).json()["error"]["code"] == "mfa_required"
    failed = admin.execute(
        "select count(*) from app.audit_log where person_id = %s and action = 'auth.mfa_failed'", (first.person_id,)
    ).fetchone()
    assert failed == (1,)


async def test_five_wrong_codes_lock_code_entry_on_every_device(browser: Browser, auth_admin: AuthAdmin) -> None:
    first = await sign_in_with_mfa(await browser(), auth_admin)
    client = await signed_in_again(browser, first.email)
    for _ in range(5):
        resp = await client.post("/v1/auth/mfa/verify", json={"code": wrong(first.secret)})
        assert resp.status_code == 400, resp.text
    right = totp(first.secret, at=time.time() + 30)
    locked = await client.post("/v1/auth/mfa/verify", json={"code": right})
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "too_many_attempts"
    other = await signed_in_again(browser, first.email)
    assert (await other.post("/v1/auth/mfa/verify", json={"code": right})).status_code == 429


async def test_wrong_codes_spread_over_more_than_15_minutes_do_not_lock(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    first = await sign_in_with_mfa(await browser(), auth_admin)
    client = await signed_in_again(browser, first.email)
    for _ in range(4):
        assert (await client.post("/v1/auth/mfa/verify", json={"code": wrong(first.secret)})).status_code == 400
    admin.execute(
        "update app.mfa_attempts set window_started_at = now() - interval '16 minutes' where person_id = %s",
        (first.person_id,),
    )
    assert (await client.post("/v1/auth/mfa/verify", json={"code": wrong(first.secret)})).status_code == 400
    ok = await client.post("/v1/auth/mfa/verify", json={"code": totp(first.secret, at=time.time() + 30)})
    assert ok.status_code == 200, ok.text


async def test_a_recovery_code_signs_out_every_other_device(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    phone = await browser()
    signed = await sign_in_with_mfa(phone, auth_admin)
    assert (await phone.get("/v1/me")).status_code == 200
    laptop = await browser()
    body = {"email": signed.email, "password": PASSWORD, "code": signed.recovery_codes[0]}
    assert (await laptop.post("/v1/auth/mfa/recover", json=body)).status_code == 200
    assert (await phone.get("/v1/me")).status_code == 401
    reasons = admin.execute(
        "select revoked_reason from app.sessions where person_id = %s and revoked_at is not null", (signed.person_id,)
    ).fetchall()
    assert ("recovery",) in reasons
    assert (await laptop.get("/v1/me")).json()["error"]["code"] == "mfa_required"
