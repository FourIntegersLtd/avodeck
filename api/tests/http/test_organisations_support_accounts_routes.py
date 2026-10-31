# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Support creates an account for a new person (permissions.md "Avodeck staff"; migrations 1012, 1013; ADR 0024).

- `POST /v1/support/accounts` {full_name, email} with an `Idempotency-Key`: current support staff only
  (anyone else 404); the sign-in MFA is enough, no fresh code (ADR 0023). FastAPI makes a temporary password
  (16 or more characters from an alphabet without look-alike characters), creates the Supabase Auth login with
  it, the `people` row (`must_change_password`) and the person's own practice. The temporary password is in
  this response only (`Cache-Control: no-store`): never stored in the database, the audit trail, logs or the
  kept Idempotency-Key answer; a repeat with the same key answers the account with no password and
  `password_shown: true`. When email is set up, the person is also sent a set-password link
  (`account.created`: the link and that it lasts 1 hour; never the password); otherwise no email, quietly.
  Support adds nobody to a team. An email that already has an account is 409 `conflict`; one Supabase Auth
  will not take is 422 `invalid_email`. Writes `support.account_created`. Any failure after the login is made
  deletes it.
- `GET /v1/support/accounts`: `invited` until the person has chosen their own password and completed MFA,
  then `active`.
- `POST /v1/support/accounts/{id}/temporary-password`: a new temporary password for an account still
  `invited`, shown once the same way; the old one stops working and the person's devices are signed out; not
  more than once a minute (429 `sent_recently`); an `active` account is 409 `conflict`. No email.
  Writes `support.account_password_renewed`.
- At most 20 new accounts per staff member per hour, refused attempts (an email that already has an account,
  one Auth will not take) counted too (429 `too_many_attempts`); one staff member's creates queue on a lock.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.auth.supabase import _unavailable
from app.core.email import ACCOUNT_CREATED, Mail
from app.features.organisations import repo as organisations_repo
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.auth import AuthAdmin, new_email, totp
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio

URL = "/v1/support/accounts"
NEW_PASSWORD = "A-long-password-of-my-own-7"


@pytest.fixture
def staff(world: World) -> uuid.UUID:
    person = world.seed.person(full_name="Support")
    world.seed.support_staff(person)
    return person


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def mails(app: FastAPI, template: str) -> list[Mail]:
    return [m for m in app.state.mailer.outbox if m.template == template]


def reset_token(mail: Mail) -> str:
    return mail.params["link"].split("?token=", 1)[1]


async def create(client_for: ClientFor, staff: uuid.UUID, **body: Any) -> dict[str, Any]:
    body.setdefault("full_name", "Dr New Expert")
    body.setdefault("email", new_email())
    resp = await (await client_for(staff)).post(URL, json=body, headers=key())
    assert resp.status_code == 201, resp.text
    created: dict[str, Any] = resp.json()
    return created


ALPHABET = set("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789-")


def looks_temporary(password: object) -> bool:
    return isinstance(password, str) and len(password.replace("-", "")) >= 16 and set(password) <= ALPHABET


async def sign_in(browser: Any, email: str, password: str) -> httpx.Response:
    web = await browser()
    response: httpx.Response = await web.post("/v1/auth/sign-in", json={"email": email, "password": password})
    return response


async def test_staff_create_an_account_and_see_its_temporary_password_once(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection,
    auth_admin: AuthAdmin, browser: Any, caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    email = new_email()
    with caplog.at_level(logging.DEBUG):
        resp = await (await client_for(staff)).post(
            URL, json={"full_name": "Dr New Expert", "email": email.upper()}, headers=key()
        )
    assert resp.status_code == 201, resp.text
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert set(body) == {
        "id", "person_id", "email", "status", "created_at", "last_sent_at", "temporary_password", "password_shown",
    }  # fmt: skip
    assert (body["email"], body["status"], body["password_shown"]) == (email, "invited", False)
    password = body["temporary_password"]
    assert looks_temporary(password), password
    person = uuid.UUID(body["person_id"])
    # Supabase Auth holds the login with the temporary password; Avodeck holds the person and their practice.
    assert auth_admin.user(person)["email"] == email
    assert (await sign_in(browser, email, password)).status_code == 200
    assert admin.execute(
        "select email, full_name, closed_at, must_change_password from app.people where id = %s", (person,)
    ).fetchone() == (email, "Dr New Expert", None, True)
    assert admin.execute(
        "select o.kind, m.role from app.memberships m join app.organisations o on o.id = m.organisation_id"
        " where m.person_id = %s",
        (person,),
    ).fetchall() == [("personal", "owner")]
    [mail] = mails(app, ACCOUNT_CREATED)  # email is set up here: a set-password link, never the password
    assert mail.to == email
    assert set(mail.params) == {"link", "link_hours"} and mail.params["link_hours"] == "1"
    assert mail.params["link"].startswith("https://app.test/reset-password?token=")
    assert password not in str(mail)
    audit = admin.execute(
        "select person_id, target from app.audit_log where action = 'support.account_created' and person_id = %s",
        (staff,),
    ).fetchall()
    assert audit == [(staff, f"person:{person}")]
    assert password not in caplog.text
    stored = admin.execute("select response_body from app.idempotency_keys where person_id = %s", (staff,)).fetchall()
    assert all(password.encode() not in bytes(row[0]) for row in stored)


async def test_a_repeat_with_the_same_key_never_shows_the_password_again(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection
) -> None:
    client = await client_for(staff)
    body = {"full_name": "Dr X", "email": new_email()}
    same = key()
    first = await client.post(URL, json=body, headers=same)
    again = await client.post(URL, json=body, headers=same)
    assert first.status_code == again.status_code == 201
    assert again.headers.get("idempotent-replayed") == "true"
    assert again.headers["cache-control"] == "no-store"
    shown, replayed = first.json(), again.json()
    assert looks_temporary(shown["temporary_password"])
    assert replayed == {**shown, "temporary_password": None, "password_shown": True}
    assert admin.execute("select count(*) from app.support_accounts where created_by = %s", (staff,)).fetchone() == (1,)
    assert len(mails(app, ACCOUNT_CREATED)) == 1


async def test_with_no_email_set_up_the_account_is_made_without_one(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, monkeypatch: pytest.MonkeyPatch,
    browser: Any,
) -> None:  # fmt: skip
    async def never(email: str) -> str:
        raise AssertionError("no set-password link without email")

    monkeypatch.setattr(app.state, "mail_configured", False)
    monkeypatch.setattr(app.state.auth, "admin_recovery_token", never)
    made = await create(client_for, staff)
    assert app.state.mailer.outbox == []
    assert (await sign_in(browser, made["email"], made["temporary_password"])).status_code == 200


async def test_the_new_person_signs_in_sets_up_mfa_chooses_a_password_and_becomes_active(
    client_for: ClientFor, world: World, staff: uuid.UUID, browser: Any, admin: psycopg.Connection
) -> None:
    made = await create(client_for, staff)
    web = await browser()
    signed = await web.post("/v1/auth/sign-in", json={"email": made["email"], "password": made["temporary_password"]})
    assert signed.status_code == 200, signed.text
    assert signed.json()["mfa_enrolled"] is False  # MFA is set up at first sign-in, by the person
    secret = (await web.post("/v1/auth/mfa/enroll")).json()["secret"]
    assert (await web.post("/v1/auth/mfa/verify", json={"code": totp(secret)})).status_code == 200
    blocked = await web.get("/v1/today")
    assert (blocked.status_code, blocked.json()["error"]["code"]) == (403, "password_change_required")
    listed = (await (await client_for(staff)).get(URL)).json()["accounts"]
    assert [a["status"] for a in listed if a["id"] == made["id"]] == ["invited"]
    changed = await web.post(
        "/v1/auth/password/change",
        json={"current_password": made["temporary_password"], "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200, changed.text
    assert (await web.get("/v1/today")).status_code == 200
    listed = (await (await client_for(staff)).get(URL)).json()["accounts"]
    assert [a["status"] for a in listed if a["id"] == made["id"]] == ["active"]


async def test_the_set_password_link_also_works_and_clears_the_temporary_password(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, browser: Any, admin: psycopg.Connection
) -> None:
    made = await create(client_for, staff)
    web = await browser()
    reset = await web.post(
        "/v1/auth/password/reset",
        json={"token": reset_token(mails(app, ACCOUNT_CREATED)[0]), "new_password": NEW_PASSWORD},
    )
    assert reset.status_code == 200, reset.text
    assert admin.execute(
        "select must_change_password from app.people where id = %s", (made["person_id"],)
    ).fetchone() == (False,)
    assert (await sign_in(browser, made["email"], made["temporary_password"])).status_code == 401


BAD_FORMS = {
    "a team and a role": {"organisation_id": "TEAM", "role": "admin"},  # a team's own owners invite people
    "a role": {"role": "assistant"},
    "a team": {"organisation_id": "TEAM"},
    "a password": {"password": "chosen-by-support-1"},  # support never chooses the password
    "not an email": {"email": "not-an-email"},
    "no name": {"full_name": ""},
}


@pytest.mark.parametrize("case", sorted(BAD_FORMS))
async def test_the_form_must_make_sense(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection, case: str
) -> None:
    body: dict[str, Any] = {"full_name": "Dr New", "email": new_email(), **BAD_FORMS[case]}
    if body.get("organisation_id") == "TEAM":
        body["organisation_id"] = str(world.agency)
    resp = await (await client_for(staff)).post(URL, json=body, headers=key())
    assert resp.status_code == 422, resp.text
    assert app.state.mailer.outbox == []
    assert admin.execute("select count(*) from app.invites where email = %s", (body.get("email", ""),)).fetchone() == (
        0,
    )
    assert admin.execute("select count(*) from app.people where email = %s", (body.get("email", ""),)).fetchone() == (
        0,
    )


async def test_staff_cannot_add_anyone_to_a_team(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection
) -> None:
    """Not even themselves, not even as admin: the create form takes no team (reviewer finding, 2026-10-25)."""
    client = await client_for(staff)
    email = new_email()
    for team in (world.agency, world.solo):
        resp = await client.post(
            URL, json={"full_name": "Dr N", "email": email, "organisation_id": str(team), "role": "admin"},
            headers=key(),
        )  # fmt: skip
        assert resp.status_code == 422, resp.text
    made = await create(client_for, staff, email=email)
    assert "organisation_id" not in made and "role" not in made
    assert admin.execute("select count(*) from app.invites where email = %s", (email,)).fetchone() == (0,)
    assert admin.execute("select count(*) from app.invites where invited_by = %s", (staff,)).fetchone() == (0,)


async def test_an_email_that_already_has_an_account_is_a_clean_conflict(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection,
    auth_admin: AuthAdmin,
) -> None:  # fmt: skip
    client = await client_for(staff)
    known = await client.post(URL, json={"full_name": "Dr K", "email": world.email("dr_k").upper()}, headers=key())
    assert (known.status_code, known.json()["error"]["code"]) == (409, "conflict")
    login_only = new_email()
    auth_admin.create_user(login_only)  # a login in Supabase Auth that never signed in to Avodeck
    again = await client.post(URL, json={"full_name": "Dr L", "email": login_only}, headers=key())
    assert (again.status_code, again.json()["error"]["code"]) == (409, "conflict")
    first = await create(client_for, staff)
    twice = await client.post(URL, json={"full_name": "Dr T", "email": first["email"]}, headers=key())
    assert (twice.status_code, twice.json()["error"]["code"]) == (409, "conflict")
    assert len(mails(app, ACCOUNT_CREATED)) == 1
    assert admin.execute("select count(*) from app.support_accounts where created_by = %s", (staff,)).fetchone() == (1,)
    # Each refused attempt stays in the audit trail (ids and codes only), though the request rolled back.
    assert (
        admin.execute(
            "select target from app.audit_log where action = 'support.account_refused' and person_id = %s order by seq",
            (staff,),
        ).fetchall()
        == [("refused:conflict",)] * 3
    )


@pytest.mark.parametrize("who", ["dr_k", "owner", "removed"])
async def test_anyone_but_current_staff_gets_404(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, who: str
) -> None:
    made = await create(client_for, staff)
    app.state.mailer.outbox.clear()
    person = world[who] if who != "removed" else world.seed.person()
    if who == "removed":
        world.seed.support_staff(person, removed=True)
    client = await client_for(person)
    assert (await client.get(URL)).status_code == 404
    created = await client.post(URL, json={"full_name": "Dr X", "email": new_email()}, headers=key())
    assert created.status_code == 404
    assert (await client.post(f"{URL}/{made['id']}/temporary-password", headers=key())).status_code == 404
    assert app.state.mailer.outbox == []


async def test_creating_and_a_new_temporary_password_need_no_fresh_mfa(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    """ADR 0023: the sign-in MFA is enough for every support action."""
    made = await create(client_for, staff)
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes'")
    client = await client_for(staff, mfa_age=STALE)
    created = await client.post(URL, json={"full_name": "Dr X", "email": new_email()}, headers=key())
    assert created.status_code == 201, created.text
    renewed = await client.post(f"{URL}/{made['id']}/temporary-password", headers=key())
    assert renewed.status_code == 200, renewed.text
    assert (await client.get(URL)).status_code == 200


async def test_creating_needs_the_csrf_token_and_an_idempotency_key(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    body = {"full_name": "Dr X", "email": new_email()}
    no_csrf = await (await client_for(staff, csrf=False)).post(URL, json=body, headers=key())
    assert (no_csrf.status_code, no_csrf.json()["error"]["code"]) == (403, "csrf_required")
    assert (await (await client_for(staff)).post(URL, json=body)).status_code == 422
    assert admin.execute("select count(*) from app.support_accounts where created_by = %s", (staff,)).fetchone() == (0,)


async def test_a_new_temporary_password_replaces_the_old_one_not_more_than_once_a_minute(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection,
    browser: Any, caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    made = await create(client_for, staff)
    app.state.mailer.outbox.clear()
    person = uuid.UUID(made["person_id"])
    client = await client_for(staff)
    url = f"{URL}/{made['id']}/temporary-password"
    soon = await client.post(url, headers=key())
    assert (soon.status_code, soon.json()["error"]["code"]) == (429, "sent_recently")
    their_device = await client_for(person, email=made["email"], aal="aal1", mfa_age=None)
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes' where id = %s",
                  (made["id"],))  # fmt: skip
    same = key()
    with caplog.at_level(logging.DEBUG):
        resp = await client.post(url, headers=same)
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    renewed = resp.json()
    password = renewed["temporary_password"]
    assert looks_temporary(password) and password != made["temporary_password"]
    assert (renewed["status"], renewed["password_shown"]) == ("invited", False)
    assert renewed["last_sent_at"] > made["last_sent_at"]
    assert (await sign_in(browser, made["email"], made["temporary_password"])).status_code == 401
    assert (await sign_in(browser, made["email"], password)).status_code == 200
    assert (await their_device.get("/v1/auth/session")).status_code == 401  # signed out everywhere
    assert app.state.mailer.outbox == []  # the password is told by phone or in person, never emailed
    assert password not in caplog.text
    again = await client.post(url, headers=same)
    assert again.status_code == 200 and again.headers.get("idempotent-replayed") == "true"
    assert again.json() == {**renewed, "temporary_password": None, "password_shown": True}
    assert admin.execute(
        "select target from app.audit_log where action = 'support.account_password_renewed' and person_id = %s",
        (staff,),
    ).fetchall() == [(f"person:{person}",)]
    assert admin.execute("select must_change_password from app.people where id = %s", (person,)).fetchone() == (True,)


async def test_an_active_account_gets_no_new_temporary_password(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    made = await create(client_for, staff)
    admin.execute("update app.people set must_change_password = false where id = %s", (made["person_id"],))
    await client_for(uuid.UUID(made["person_id"]), email=made["email"])  # a session with MFA done: active
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes'")
    client = await client_for(staff)
    resp = await client.post(f"{URL}/{made['id']}/temporary-password", headers=key())
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "conflict")
    assert (await client.post(f"{URL}/{uuid.uuid4()}/temporary-password", headers=key())).status_code == 404


async def test_at_most_twenty_new_accounts_an_hour_per_staff_member(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    for _ in range(20):
        person = world.seed.person()
        admin.execute("insert into app.support_accounts (person_id, created_by) values (%s, %s)", (person, staff))
    resp = await (await client_for(staff)).post(URL, json={"full_name": "Dr X", "email": new_email()}, headers=key())
    assert (resp.status_code, resp.json()["error"]["code"]) == (429, "too_many_attempts")


async def test_refused_attempts_count_toward_the_twenty_an_hour(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection, auth_admin: AuthAdmin
) -> None:
    """Probing which emails have an account uses up the hour as well."""
    for _ in range(18):
        person = world.seed.person()
        admin.execute("insert into app.support_accounts (person_id, created_by) values (%s, %s)", (person, staff))
    client = await client_for(staff)
    known = await client.post(URL, json={"full_name": "Dr K", "email": world.email("dr_k")}, headers=key())
    assert known.status_code == 409
    login_only = new_email()
    auth_admin.create_user(login_only)
    probe = await client.post(URL, json={"full_name": "Dr L", "email": login_only}, headers=key())
    assert probe.status_code == 409
    resp = await client.post(URL, json={"full_name": "Dr X", "email": new_email()}, headers=key())
    assert (resp.status_code, resp.json()["error"]["code"]) == (429, "too_many_attempts")


async def test_if_the_set_password_link_cannot_be_made_the_login_is_undone(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    async def down(email: str) -> str:
        raise _unavailable()

    email = new_email()
    with monkeypatch.context() as patched:
        patched.setattr(app.state.auth, "admin_recovery_token", down)
        resp = await (await client_for(staff)).post(URL, json={"full_name": "Dr X", "email": email}, headers=key())
    assert (resp.status_code, resp.json()["error"]["code"]) == (503, "auth_unavailable")
    assert admin.execute("select count(*) from app.people where email = %s", (email,)).fetchone() == (0,)
    assert app.state.mailer.outbox == []
    made = await create(client_for, staff, email=email)  # the login was deleted, so the email is free again
    assert made["email"] == email


async def test_if_the_database_work_fails_the_login_is_undone(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database went away")

    email = new_email()
    with monkeypatch.context() as patched:
        patched.setattr(organisations_repo, "support_accounts", broken)  # after the rows are written
        with pytest.raises(RuntimeError):
            await (await client_for(staff)).post(URL, json={"full_name": "Dr X", "email": email}, headers=key())
    assert admin.execute("select count(*) from app.people where email = %s", (email,)).fetchone() == (0,)
    assert app.state.mailer.outbox == []
    made = await create(client_for, staff, email=email)
    assert made["email"] == email


async def test_a_password_reset_from_the_email_link_is_never_replaced(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, browser: Any, admin: psycopg.Connection
) -> None:
    made = await create(client_for, staff)
    web = await browser()
    token = reset_token(mails(app, ACCOUNT_CREATED)[0])
    reset = await web.post("/v1/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD})
    assert reset.status_code == 200, reset.text
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes' where id = %s",
                  (made["id"],))  # fmt: skip
    resp = await (await client_for(staff)).post(f"{URL}/{made['id']}/temporary-password", headers=key())
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "conflict")
    assert (await sign_in(browser, made["email"], NEW_PASSWORD)).status_code == 200


async def test_a_person_with_mfa_set_up_keeps_their_password(
    client_for: ClientFor, world: World, staff: uuid.UUID, browser: Any, admin: psycopg.Connection
) -> None:
    """Checked in the database (a session with MFA) and, should no session say so, with Supabase Auth."""
    made = await create(client_for, staff)
    web = await browser()
    signed = await web.post("/v1/auth/sign-in", json={"email": made["email"], "password": made["temporary_password"]})
    assert signed.status_code == 200, signed.text
    secret = (await web.post("/v1/auth/mfa/enroll")).json()["secret"]
    assert (await web.post("/v1/auth/mfa/verify", json={"code": totp(secret)})).status_code == 200
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes' where id = %s",
                  (made["id"],))  # fmt: skip
    client = await client_for(staff)
    url = f"{URL}/{made['id']}/temporary-password"
    resp = await client.post(url, headers=key())
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "conflict")
    admin.execute("delete from app.sessions where person_id = %s", (made["person_id"],))
    resp = await client.post(url, headers=key())
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "conflict")
    assert (await sign_in(browser, made["email"], made["temporary_password"])).status_code == 200
