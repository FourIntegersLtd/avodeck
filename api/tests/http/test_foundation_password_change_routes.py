"""`POST /v1/auth/password/change`: a signed-in person changes their password (ADR 0024; S01, T27).

- Needs the session's CSRF token and MFA completed at sign-in (like every signed-in route) and the
  current password; the new one follows the reset rules (12 or more characters; Supabase Auth may refuse
  a weak one, 422 `weak_password`) and must differ from the current one (422 `same_password`).
- A wrong current password is 400 `wrong_password` (not 401: the person stays signed in) and is audited
  as `auth.password_change_failed`; nothing changes.
- On success the old password stops working, `must_change_password` is cleared, the person's other
  devices are signed out (this one stays), and `auth.password_changed` is audited (ids only).
- A password reset from the email link also clears `must_change_password`.
- Five wrong current passwords in 15 minutes lock the change for the rest of the window (429
  `too_many_attempts`, even with the right one); the count is kept though the refused request rolls back.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import httpx
import psycopg
import pytest

from tests.support.auth import PASSWORD, AuthAdmin, new_email, sign_in_with_mfa

pytestmark = pytest.mark.anyio

URL = "/v1/auth/password/change"
NEW = "My-own-new-password-42"
Browser = Callable[[], Awaitable[httpx.AsyncClient]]


def flagged(admin: psycopg.Connection, person: object) -> bool:
    row = admin.execute("select must_change_password from app.people where id = %s", (person,)).fetchone()
    assert row is not None
    return bool(row[0])


async def test_changing_the_password_clears_the_block_and_the_old_one_stops_working(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    web = await browser()
    signed = await sign_in_with_mfa(web, auth_admin)
    other_device = await browser()
    assert (
        await other_device.post("/v1/auth/sign-in", json={"email": signed.email, "password": PASSWORD})
    ).status_code == 200
    admin.execute("update app.people set must_change_password = true where id = %s", (signed.person_id,))
    assert (await web.get("/v1/today")).status_code == 403
    with caplog.at_level(logging.DEBUG):
        resp = await web.post(URL, json={"current_password": PASSWORD, "new_password": NEW})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "password_changed"}
    assert resp.headers["cache-control"] == "no-store"
    assert not flagged(admin, signed.person_id)
    assert (await web.get("/v1/today")).status_code == 200  # this device carries on
    assert (await other_device.get("/v1/auth/session")).status_code == 401  # the others are signed out
    fresh = await browser()
    old = await fresh.post("/v1/auth/sign-in", json={"email": signed.email, "password": PASSWORD})
    assert old.status_code == 401
    assert (await fresh.post("/v1/auth/sign-in", json={"email": signed.email, "password": NEW})).status_code == 200
    assert admin.execute(
        "select target from app.audit_log where action = 'auth.password_changed' and person_id = %s",
        (signed.person_id,),
    ).fetchall() == [(f"person:{signed.person_id}",)]
    assert NEW not in caplog.text and PASSWORD not in caplog.text


async def test_a_wrong_current_password_changes_nothing(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    web = await browser()
    signed = await sign_in_with_mfa(web, auth_admin)
    admin.execute("update app.people set must_change_password = true where id = %s", (signed.person_id,))
    resp = await web.post(URL, json={"current_password": "Not-my-password-1", "new_password": NEW})
    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "wrong_password")
    assert flagged(admin, signed.person_id)
    assert (await web.get("/v1/auth/session")).status_code == 200  # still signed in
    assert admin.execute(
        "select count(*) from app.audit_log where action = 'auth.password_change_failed' and person_id = %s",
        (signed.person_id,),
    ).fetchone() == (1,)


@pytest.mark.parametrize(("new", "status", "code"), [("short-1", 422, "invalid"), (PASSWORD, 422, "same_password")])
async def test_the_new_password_must_be_long_and_different(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection, new: str, status: int, code: str
) -> None:
    web = await browser()
    signed = await sign_in_with_mfa(web, auth_admin)
    resp = await web.post(URL, json={"current_password": PASSWORD, "new_password": new})
    assert resp.status_code == status, resp.text
    if code != "invalid":
        assert resp.json()["error"]["code"] == code
    fresh = await browser()
    assert (await fresh.post("/v1/auth/sign-in", json={"email": signed.email, "password": PASSWORD})).status_code == 200


async def test_it_needs_the_csrf_token(browser: Browser, auth_admin: AuthAdmin) -> None:
    web = await browser()
    await sign_in_with_mfa(web, auth_admin)
    bare = httpx.AsyncClient(transport=web._transport, base_url="https://test", cookies=web.cookies)
    async with bare:
        resp = await bare.post(URL, json={"current_password": PASSWORD, "new_password": NEW})
    assert (resp.status_code, resp.json()["error"]["code"]) == (403, "csrf_required")


async def test_a_reset_from_the_email_link_also_clears_the_block(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    email = new_email()
    person = auth_admin.create_user(email)
    web = await browser()
    assert (await web.post("/v1/auth/sign-in", json={"email": email, "password": PASSWORD})).status_code == 200
    admin.execute("update app.people set must_change_password = true where id = %s", (person,))
    reset = await web.post(
        "/v1/auth/password/reset", json={"token": auth_admin.recovery_token(email), "new_password": NEW}
    )
    assert reset.status_code == 200, reset.text
    assert not flagged(admin, person)


async def test_five_wrong_current_passwords_lock_the_change_for_15_minutes(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    web = await browser()
    signed = await sign_in_with_mfa(web, auth_admin)
    admin.execute("update app.people set must_change_password = true where id = %s", (signed.person_id,))
    for _ in range(5):
        wrong = await web.post(URL, json={"current_password": "Not-my-password-1", "new_password": NEW})
        assert (wrong.status_code, wrong.json()["error"]["code"]) == (400, "wrong_password")
    locked = await web.post(URL, json={"current_password": PASSWORD, "new_password": NEW})
    assert (locked.status_code, locked.json()["error"]["code"]) == (429, "too_many_attempts")
    assert flagged(admin, signed.person_id)
    fresh = await browser()
    assert (await fresh.post("/v1/auth/sign-in", json={"email": signed.email, "password": PASSWORD})).status_code == 200
    assert admin.execute(
        "select failures from app.password_change_attempts where person_id = %s", (signed.person_id,)
    ).fetchone() == (5,)
    admin.execute(
        "update app.password_change_attempts set window_started_at = now() - interval '16 minutes'"
        " where person_id = %s",
        (signed.person_id,),
    )
    done = await web.post(URL, json={"current_password": PASSWORD, "new_password": NEW})
    assert done.status_code == 200, done.text
    assert admin.execute(
        "select failures from app.password_change_attempts where person_id = %s", (signed.person_id,)
    ).fetchone() == (0,)
