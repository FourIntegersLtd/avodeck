"""Device sessions, inactivity expiry, password reset and email change (architecture 5.2, 9, 10; S28)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from tests.support.auth import PASSWORD, AuthAdmin, new_email, sign_in_with_mfa
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

Browser = Callable[[], Awaitable[httpx.AsyncClient]]
ClientWith = Callable[..., Awaitable[httpx.AsyncClient]]
NEW_PASSWORD = "Another-long-password-42"


def session_row(admin: psycopg.Connection, sid: object) -> tuple[object, ...]:
    row = admin.execute(
        "select revoked_at is not null, revoked_reason, last_seen_at from app.sessions where id = %s", (sid,)
    ).fetchone()
    assert row is not None
    return row


def token_from(link: str) -> str:
    return parse_qs(urlsplit(link).query)["token"][0]


# --- inactivity and lifetime --------------------------------------------------------------------


async def test_thirty_minutes_without_activity_ends_the_session(
    seed: Seed, client_with: ClientWith, admin: psycopg.Connection
) -> None:
    person = seed.person()
    session = seed.session(person, idle=timedelta(minutes=31))
    resp = await (await client_with(session)).get("/v1/auth/session")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_expired"
    assert session_row(admin, session.id)[:2] == (True, "idle")


async def test_activity_inside_thirty_minutes_keeps_the_session_alive(
    seed: Seed, client_with: ClientWith, admin: psycopg.Connection
) -> None:
    person = seed.person()
    session = seed.session(person, idle=timedelta(minutes=29))
    before = session_row(admin, session.id)[2]
    assert (await (await client_with(session)).get("/v1/me")).status_code == 200
    revoked, _, after = session_row(admin, session.id)
    assert not revoked and after > before  # type: ignore[operator]


async def test_checking_the_session_does_not_count_as_activity(
    seed: Seed, client_with: ClientWith, admin: psycopg.Connection
) -> None:
    person = seed.person()
    session = seed.session(person, idle=timedelta(minutes=5))
    before = session_row(admin, session.id)[2]
    resp = await (await client_with(session)).get("/v1/auth/session")
    assert resp.status_code == 200
    assert 24 <= resp.json()["minutes_until_timeout"] <= 25
    assert session_row(admin, session.id)[2] == before


async def test_a_session_ends_after_twelve_hours_even_when_active(
    seed: Seed, client_with: ClientWith, admin: psycopg.Connection
) -> None:
    person = seed.person()
    session = seed.session(person, age=timedelta(hours=13))
    resp = await (await client_with(session)).get("/v1/me")
    assert resp.json()["error"]["code"] == "session_expired"
    assert session_row(admin, session.id)[:2] == (True, "max_age")


# --- devices (S28) ------------------------------------------------------------------------------


async def test_a_person_sees_and_signs_out_their_own_devices_only(
    seed: Seed, client_with: ClientWith, admin: psycopg.Connection
) -> None:
    person, other = seed.person(), seed.person()
    here = seed.session(person, device_label="Laptop")
    phone = seed.session(person, device_label="Phone")
    theirs = seed.session(other)
    client = await client_with(here)

    listed = (await client.get("/v1/me/sessions")).json()
    assert {(s["device_label"], s["current"]) for s in listed} == {("Laptop", True), ("Phone", False)}
    assert {"id", "created_at", "last_seen_at"} <= set(listed[0])

    assert (await client.delete(f"/v1/me/sessions/{theirs.id}")).status_code == 404
    assert session_row(admin, theirs.id)[0] is False
    assert (await client.delete(f"/v1/me/sessions/{phone.id}")).status_code == 204
    assert session_row(admin, phone.id)[:2] == (True, "signed_out")
    assert (await (await client_with(phone)).get("/v1/me")).status_code == 401
    assert [s["device_label"] for s in (await client.get("/v1/me/sessions")).json()] == ["Laptop"]


async def test_sign_out_everywhere_ends_every_session(
    seed: Seed, client_with: ClientWith, admin: psycopg.Connection
) -> None:
    person = seed.person()
    here, there = seed.session(person), seed.session(person)
    resp = await (await client_with(here)).post("/v1/me/sessions/revoke-all")
    assert resp.status_code == 204
    assert session_row(admin, here.id)[0] and session_row(admin, there.id)[0]
    actions = admin.execute(
        "select action from app.audit_log where person_id = %s and action = 'auth.sessions_revoked'", (person,)
    ).fetchall()
    assert actions == [("auth.sessions_revoked",)]


# --- password reset -----------------------------------------------------------------------------


async def test_forgot_password_answers_the_same_whether_or_not_the_email_exists(
    browser: Browser, auth_admin: AuthAdmin
) -> None:
    known = new_email()
    auth_admin.create_user(known)
    replies = []
    for email in (known, new_email()):
        resp = await (await browser()).post("/v1/auth/password/forgot", json={"email": email})
        replies.append((resp.status_code, resp.json(), resp.headers.get("set-cookie")))
    assert replies[0] == replies[1] == (202, {"status": "sent"}, None)


async def test_resetting_the_password_signs_out_every_device(
    browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    device = await browser()
    signed = await sign_in_with_mfa(device, auth_admin)
    token = auth_admin.recovery_token(signed.email)

    resp = await (await browser()).post("/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "password_changed"}
    assert (await device.get("/v1/me")).status_code == 401
    (live,) = admin.execute(  # type: ignore[misc]
        "select count(*) from app.sessions where person_id = %s and revoked_at is null", (signed.person_id,)
    ).fetchone()
    assert live == 0

    old = await (await browser()).post("/v1/auth/sign-in", json={"email": signed.email, "password": PASSWORD})
    assert old.status_code == 401
    new = await (await browser()).post("/v1/auth/sign-in", json={"email": signed.email, "password": NEW_PASSWORD})
    assert new.json() == {"status": "signed_in", "mfa_enrolled": True}

    again = await (await browser()).post("/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD})
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_token"


async def test_reset_refuses_a_short_password(browser: Browser, auth_admin: AuthAdmin) -> None:
    email = new_email()
    auth_admin.create_user(email)
    token = auth_admin.recovery_token(email)
    resp = await (await browser()).post("/v1/auth/password/reset", json={"token": token, "new_password": "short"})
    assert resp.status_code == 422


# --- email change -------------------------------------------------------------------------------


async def test_email_change_needs_both_addresses_to_confirm(
    app: FastAPI, browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    client = await browser()
    signed = await sign_in_with_mfa(client, auth_admin)
    await client.get("/v1/me")
    target = new_email()

    resp = await client.post("/v1/me/email-change", json={"new_email": target})
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "confirmation_sent"}
    mails = {m.to: m for m in app.state.mailer.outbox if m.template.startswith("email_change.")}
    assert set(mails) == {signed.email, target}
    old_token = token_from(mails[signed.email].params["link"])
    new_token = token_from(mails[target].params["link"])
    assert old_token != new_token

    anyone = await browser()
    first = await anyone.post("/v1/me/email-change/confirm", json={"token": old_token})
    assert first.json() == {"status": "waiting_for_other_address"}
    assert auth_admin.user(signed.person_id)["email"] == signed.email

    second = await anyone.post("/v1/me/email-change/confirm", json={"token": new_token})
    assert second.status_code == 200, second.text
    assert second.json() == {"status": "email_changed"}
    assert auth_admin.user(signed.person_id)["email"] == target
    assert admin.execute("select email from app.people where id = %s", (signed.person_id,)).fetchone() == (target,)
    assert (await client.get("/v1/me")).json()["person"]["email"] == target

    reused = await anyone.post("/v1/me/email-change/confirm", json={"token": new_token})
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "invalid_token"


async def test_an_email_change_link_expires_after_a_day(
    app: FastAPI, browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    client = await browser()
    signed = await sign_in_with_mfa(client, auth_admin)
    await client.get("/v1/me")
    await client.post("/v1/me/email-change", json={"new_email": new_email()})
    admin.execute(
        "update app.email_changes set expires_at = now() - interval '1 minute' where person_id = %s",
        (signed.person_id,),
    )
    link = next(m for m in app.state.mailer.outbox if m.to == signed.email).params["link"]
    resp = await (await browser()).post("/v1/me/email-change/confirm", json={"token": token_from(link)})
    assert resp.status_code == 400


async def test_email_change_needs_recent_mfa(seed: Seed, client_for: ClientFor) -> None:
    person = seed.person()
    resp = await (await client_for(person, mfa_age=timedelta(minutes=11))).post(
        "/v1/me/email-change", json={"new_email": new_email()}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "step_up_required"
