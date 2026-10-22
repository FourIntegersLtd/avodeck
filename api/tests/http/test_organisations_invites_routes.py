# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Invites (architecture 5.4, 10.1 "Invites"; state-machines.md Invite).

- `GET/POST /v1/organisations/{org_id}/invites` 🔒: an owner or admin lists and sends invites (an admin
  cannot invite an owner). Sending emails `invite_mail` with the accept link; only the token's hash is kept.
- `DELETE /v1/organisations/{org_id}/invites/{invite_id}` 🔒: cancels a pending invite.
- `POST /v1/invites/{token}/accept`: signed in as the invited email (any case), before `expires_at`
  (7 days), once. It creates the membership. Anyone else, or a token that does not exist, gets 404.
- States: pending -> accepted / cancelled / expired, all terminal (migration 1002).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.email import ORG_INVITE, Mail
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio


def invites_url(w: World, org: uuid.UUID | None = None) -> str:
    return f"/v1/organisations/{org or w.agency}/invites"


def invite_mails(app: FastAPI) -> list[Mail]:
    return [m for m in app.state.mailer.outbox if m.template == ORG_INVITE]


def token_of(mail: Mail) -> str:
    return mail.params["link"].rsplit("/", 1)[1]


async def send(client_for: ClientFor, w: World, *, by: str = "admin", email: str, role: str = "assistant") -> dict:  # type: ignore[type-arg]
    resp = await (await client_for(w[by])).post(invites_url(w), json={"email": email, "role": role})
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


@pytest.mark.parametrize("by", ["owner", "admin"])
async def test_owner_or_admin_sends_an_invite_by_email(
    client_for: ClientFor, world: World, app: FastAPI, admin: psycopg.Connection, by: str
) -> None:
    before = datetime.now(UTC)
    body = await send(client_for, world, by=by, email=world.email("outsider").upper())
    assert (body["email"], body["role"], body["status"]) == (world.email("outsider"), "assistant", "pending")
    assert "token" not in body
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(days=7) - timedelta(minutes=1) <= expires - before <= timedelta(days=7, minutes=1)
    [mail] = invite_mails(app)
    assert mail.to == world.email("outsider")
    assert mail.params["org_ref"] == str(world.agency) and mail.params["role"] == "assistant"
    assert mail.params["link"].startswith("https://app.test/invites/")
    token = token_of(mail)
    stored = admin.execute("select token_hash from app.invites where id = %s", (body["id"],)).fetchone()
    assert stored == (hashlib.sha256(token.encode()).hexdigest(),)
    assert admin.execute(
        "select count(*) from app.audit_log where organisation_id = %s and action = 'invite.sent'", (world.agency,)
    ).fetchone() == (1,)


async def test_owners_and_admins_list_invites(client_for: ClientFor, world: World) -> None:
    sent = await send(client_for, world, email="new.person@example.test")
    resp = await (await client_for(world["owner"])).get(invites_url(world))
    assert resp.status_code == 200, resp.text
    assert [i["id"] for i in resp.json()["invites"]] == [sent["id"]]


@pytest.mark.parametrize("who", ["dr_k", "assistant", "viewer"])
async def test_other_members_cannot_invite_or_list(client_for: ClientFor, world: World, who: str) -> None:
    client = await client_for(world[who])
    assert (await client.get(invites_url(world))).status_code == 403
    resp = await client.post(invites_url(world), json={"email": "a@example.test", "role": "viewer"})
    assert resp.status_code == 403


async def test_non_members_get_404(client_for: ClientFor, world: World) -> None:
    client = await client_for(world["outsider"])
    assert (await client.get(invites_url(world))).status_code == 404


async def test_an_admin_cannot_invite_an_owner_but_an_owner_can(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["admin"])).post(
        invites_url(world), json={"email": "boss@example.test", "role": "owner"}
    )
    assert resp.status_code == 403
    await send(client_for, world, by="owner", email="boss@example.test", role="owner")


async def test_sending_and_listing_need_step_up(client_for: ClientFor, world: World, app: FastAPI) -> None:
    client = await client_for(world["owner"], mfa_age=STALE)
    for resp in (
        await client.get(invites_url(world)),
        await client.post(invites_url(world), json={"email": "a@example.test", "role": "viewer"}),
    ):
        assert (resp.status_code, resp.json()["error"]["code"]) == (403, "step_up_required")
    assert invite_mails(app) == []


@pytest.mark.parametrize("email", ["not-an-email", "", "a@b@c"])
async def test_an_invite_needs_an_email_address(client_for: ClientFor, world: World, email: str) -> None:
    resp = await (await client_for(world["owner"])).post(invites_url(world), json={"email": email, "role": "viewer"})
    assert resp.status_code == 422


async def test_a_personal_organisation_takes_no_invites(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["dr_k"])).post(
        invites_url(world, world.solo), json={"email": "a@example.test", "role": "assistant"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "personal_organisation"


async def test_the_invited_person_accepts_once_and_becomes_a_member(
    client_for: ClientFor, world: World, app: FastAPI, admin: psycopg.Connection
) -> None:
    invite = await send(client_for, world, email=world.email("outsider"), role="viewer")
    token = token_of(invite_mails(app)[0])
    client = await client_for(world["outsider"])
    resp = await client.post(f"/v1/invites/{token}/accept")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["organisation_id"], body["role"]) == (str(world.agency), "viewer")
    assert world.membership_row(world.agency, "outsider") == ("viewer", False)
    assert admin.execute("select status, accepted_by from app.invites where id = %s", (invite["id"],)).fetchone() == (
        "accepted",
        world["outsider"],
    )
    again = await client.post(f"/v1/invites/{token}/accept")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "invite_closed")
    assert admin.execute(
        "select count(*) from app.memberships where organisation_id = %s and person_id = %s",
        (world.agency, world["outsider"]),
    ).fetchone() == (1,)


async def test_only_the_invited_email_can_accept(client_for: ClientFor, world: World, app: FastAPI) -> None:
    await send(client_for, world, email=world.email("outsider"))
    token = token_of(invite_mails(app)[0])
    resp = await (await client_for(world["dr_m"])).post(f"/v1/invites/{token}/accept")
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "not_found")
    assert world.membership_row(world.agency, "outsider") is None


async def test_an_unknown_token_is_404(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["outsider"])).post(f"/v1/invites/{'x' * 43}/accept")
    assert resp.status_code == 404


async def test_an_invite_expires_after_seven_days(
    client_for: ClientFor, world: World, app: FastAPI, admin: psycopg.Connection
) -> None:
    invite = await send(client_for, world, email=world.email("outsider"))
    admin.execute("update app.invites set expires_at = now() - interval '1 second' where id = %s", (invite["id"],))
    resp = await (await client_for(world["outsider"])).post(f"/v1/invites/{token_of(invite_mails(app)[0])}/accept")
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "invite_expired")
    assert world.membership_row(world.agency, "outsider") is None
    listed = (await (await client_for(world["owner"])).get(invites_url(world))).json()["invites"]
    assert [i["status"] for i in listed] == ["expired"]


async def test_cancelling_a_pending_invite(
    client_for: ClientFor, world: World, app: FastAPI, admin: psycopg.Connection
) -> None:
    invite = await send(client_for, world, email=world.email("outsider"))
    client = await client_for(world["owner"])
    resp = await client.delete(f"{invites_url(world)}/{invite['id']}")
    assert resp.status_code == 204, resp.text
    assert admin.execute("select status from app.invites where id = %s", (invite["id"],)).fetchone() == ("cancelled",)
    accept = await (await client_for(world["outsider"])).post(f"/v1/invites/{token_of(invite_mails(app)[0])}/accept")
    assert (accept.status_code, accept.json()["error"]["code"]) == (409, "invite_closed")
    again = await client.delete(f"{invites_url(world)}/{invite['id']}")
    assert again.status_code == 409
    assert (await client.delete(f"{invites_url(world)}/{uuid.uuid4()}")).status_code == 404


async def test_someone_already_a_member_cannot_accept_again(client_for: ClientFor, world: World, app: FastAPI) -> None:
    await send(client_for, world, email=world.email("dr_m"))
    resp = await (await client_for(world["dr_m"])).post(f"/v1/invites/{token_of(invite_mails(app)[0])}/accept")
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "already_member")


# --- the database repeats it ----------------------------------------------------------------------------


async def test_invite_states_are_terminal(admin: psycopg.Connection, world: World) -> None:
    iid = admin.execute(
        "insert into app.invites (organisation_id, email, role, invited_by, token_hash, status)"
        " values (%s, 'a@example.test', 'viewer', %s, %s, 'cancelled') returning id",
        (world.agency, world["owner"], uuid.uuid4().hex * 2),
    ).fetchone()
    assert iid is not None
    with pytest.raises(psycopg.Error):
        admin.execute("update app.invites set status = 'pending' where id = %s", (iid[0],))


async def test_only_owners_and_admins_see_invites(api_db: Database, world: World, admin: psycopg.Connection) -> None:
    admin.execute(
        "insert into app.invites (organisation_id, email, role, invited_by, token_hash)"
        " values (%s, 'a@example.test', 'viewer', %s, %s)",
        (world.agency, world["owner"], uuid.uuid4().hex * 2),
    )
    for who, seen in (("viewer", 0), ("outsider", 0), ("admin", 1)):
        async with api_db.request(scope_for(world[who])) as conn:
            cur = await conn.execute("select count(*) from app.invites where organisation_id = %s", (world.agency,))
            assert await cur.fetchone() == (seen,), who
