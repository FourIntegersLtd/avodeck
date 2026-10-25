# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Support creates an account for a new person (permissions.md "Avodeck staff"; migration 1012).

- `POST /v1/support/accounts` 🔒 {full_name, email} with an `Idempotency-Key`:
  current support staff only (anyone else 404), MFA in the last 10 minutes (403 `step_up_required`, asked
  first). FastAPI creates the Supabase Auth user with no password, the `people` row and the person's own
  practice, and emails a set-password link (`account.created`: the web app's reset page and its token, and
  that it lasts 1 hour; nothing else) once the database work has succeeded. Support adds nobody to a team: a
  team's own owners and admins invite them. An email that already has an account is 409 `conflict`; an email
  Supabase Auth will not take is 422 `invalid_email`. Writes `support.account_created` to the audit trail. If
  anything fails after the login is made, the login is deleted. Support never sees or chooses the password.
- `GET /v1/support/accounts`: the accounts support created, `invited` until the person first completes MFA,
  then `active`. Session MFA is enough (a read-only lookup).
- `POST /v1/support/accounts/{id}/resend` 🔒: a new set-password link for an account still `invited`; not
  more than once a minute (429 `sent_recently`).
- At most 20 new accounts per staff member per hour, refused attempts (an email that already has an account,
  one Auth will not take) counted too (429 `too_many_attempts`); one staff member's creates queue on a lock.
"""

from __future__ import annotations

import uuid
from typing import Any

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


async def test_staff_create_an_account_and_the_person_gets_a_set_password_email(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection,
    auth_admin: AuthAdmin,
) -> None:  # fmt: skip
    email = new_email()
    body = await create(client_for, staff, full_name="Dr New Expert", email=email.upper())
    assert set(body) == {"id", "person_id", "email", "status", "created_at", "last_sent_at"}
    assert (body["email"], body["status"]) == (email, "invited")
    person = uuid.UUID(body["person_id"])
    # Supabase Auth holds the login (no password yet); Avodeck holds the person and their own practice.
    assert auth_admin.user(person)["email"] == email
    assert admin.execute("select email, full_name, closed_at from app.people where id = %s", (person,)).fetchone() == (
        email, "Dr New Expert", None,
    )  # fmt: skip
    assert admin.execute(
        "select o.kind, m.role from app.memberships m join app.organisations o on o.id = m.organisation_id"
        " where m.person_id = %s",
        (person,),
    ).fetchall() == [("personal", "owner")]
    [mail] = mails(app, ACCOUNT_CREATED)
    assert mail.to == email
    assert mail.params["link_hours"] == "1"  # the email says plainly the link lasts 1 hour
    assert set(mail.params) == {"link", "link_hours"}
    assert mail.params["link"].startswith("https://app.test/reset-password?token=")
    assert "password" not in str(body).lower().replace("last_sent_at", "")
    audit = admin.execute(
        "select person_id, target from app.audit_log where action = 'support.account_created' and person_id = %s",
        (staff,),
    ).fetchall()
    assert audit == [(staff, f"person:{person}")]


async def test_the_new_person_sets_their_own_password_then_mfa_and_becomes_active(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, browser: Any
) -> None:
    email = new_email()
    body = await create(client_for, staff, email=email)
    web = await browser()
    reset = await web.post(
        "/v1/auth/password/reset",
        json={"token": reset_token(mails(app, ACCOUNT_CREATED)[0]), "new_password": NEW_PASSWORD},
    )
    assert reset.status_code == 200, reset.text
    signed = await web.post("/v1/auth/sign-in", json={"email": email, "password": NEW_PASSWORD})
    assert signed.status_code == 200, signed.text
    assert signed.json()["mfa_enrolled"] is False  # MFA is set up at first sign-in, by the person
    listed = (await (await client_for(staff)).get(URL)).json()["accounts"]
    assert [a["status"] for a in listed if a["id"] == body["id"]] == ["invited"]
    secret = (await web.post("/v1/auth/mfa/enroll")).json()["secret"]
    assert (await web.post("/v1/auth/mfa/verify", json={"code": totp(secret)})).status_code == 200
    listed = (await (await client_for(staff)).get(URL)).json()["accounts"]
    assert [a["status"] for a in listed if a["id"] == body["id"]] == ["active"]


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
    assert (await client.post(f"{URL}/{made['id']}/resend", headers=key())).status_code == 404
    assert app.state.mailer.outbox == []


@pytest.mark.parametrize("who", ["staff", "dr_k"])
async def test_creating_and_resending_need_mfa_in_the_last_ten_minutes(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, who: str
) -> None:
    made = await create(client_for, staff)
    app.state.mailer.outbox.clear()
    client = await client_for(staff if who == "staff" else world[who], mfa_age=STALE)
    for resp in (
        await client.post(URL, json={"full_name": "Dr X", "email": new_email()}, headers=key()),
        await client.post(f"{URL}/{made['id']}/resend", headers=key()),
    ):
        assert (resp.status_code, resp.json()["error"]["code"]) == (403, "step_up_required")
    assert app.state.mailer.outbox == []


async def test_the_list_needs_only_the_sign_in_mfa(client_for: ClientFor, world: World, staff: uuid.UUID) -> None:
    made = await create(client_for, staff)
    resp = await (await client_for(staff, mfa_age=STALE)).get(URL)
    assert resp.status_code == 200, resp.text
    assert made["id"] in [a["id"] for a in resp.json()["accounts"]]


async def test_creating_needs_the_csrf_token_and_an_idempotency_key(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection
) -> None:
    body = {"full_name": "Dr X", "email": new_email()}
    no_csrf = await (await client_for(staff, csrf=False)).post(URL, json=body, headers=key())
    assert (no_csrf.status_code, no_csrf.json()["error"]["code"]) == (403, "csrf_required")
    client = await client_for(staff)
    assert (await client.post(URL, json=body)).status_code == 422
    same = key()
    first = await client.post(URL, json=body, headers=same)
    again = await client.post(URL, json=body, headers=same)
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json() and again.headers.get("idempotent-replayed") == "true"
    assert admin.execute("select count(*) from app.support_accounts where created_by = %s", (staff,)).fetchone() == (1,)
    assert len(mails(app, ACCOUNT_CREATED)) == 1


async def test_send_the_invite_again_gives_a_new_link_not_more_than_once_a_minute(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection
) -> None:
    made = await create(client_for, staff)
    client = await client_for(staff)
    soon = await client.post(f"{URL}/{made['id']}/resend", headers=key())
    assert (soon.status_code, soon.json()["error"]["code"]) == (429, "sent_recently")
    assert len(mails(app, ACCOUNT_CREATED)) == 1
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes' where id = %s",
                  (made["id"],))  # fmt: skip
    resp = await client.post(f"{URL}/{made['id']}/resend", headers=key())
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "invited" and resp.json()["last_sent_at"] > made["last_sent_at"]
    first, second = mails(app, ACCOUNT_CREATED)
    assert reset_token(first) != reset_token(second) and second.params["link_hours"] == "1"
    assert admin.execute(
        "select count(*) from app.audit_log where action = 'support.account_invite_resent' and person_id = %s",
        (staff,),
    ).fetchone() == (1,)


async def test_an_active_account_is_not_sent_the_invite_again(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    made = await create(client_for, staff)
    await client_for(uuid.UUID(made["person_id"]), email=made["email"])  # a session with MFA done: active
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes'")
    resp = await (await client_for(staff)).post(f"{URL}/{made['id']}/resend", headers=key())
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "conflict")
    assert (await (await client_for(staff)).post(f"{URL}/{uuid.uuid4()}/resend", headers=key())).status_code == 404


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
