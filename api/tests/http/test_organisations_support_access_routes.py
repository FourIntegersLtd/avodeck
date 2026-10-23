# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Support access to one case (architecture 5.4, 10.1 "Support access"; scenario S17; F38; permissions.md).

- `POST /v1/cases/{case_id}/support-access` 🔒 (`support_access.grant`: the named expert only) gives Avodeck
  support 24 hours on this case, for one staff member or any (`granted_to` empty); a short reason code, never
  free text. A new grant ends the case's earlier active one (one active grant per case). The expert gets
  `support_access_granted_mail`.
- `DELETE /v1/cases/{case_id}/support-access/{grant_id}` 🔒 ends it early; ended is final.
- `GET /v1/cases/{case_id}/support-access`: the grants, shown on the case to anyone who sees it, with whether
  each is active now (expiry is checked on every read).
- F38 / S17: the grant and every view under it are in the case's audit trail (`GET /v1/cases/{id}/audit`).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.email import SUPPORT_ACCESS_GRANTED, OutboxMailer
from app.features.organisations import support
from app.features.organisations.schemas import SupportGrantCreate
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio


def url(case: uuid.UUID, grant: uuid.UUID | str | None = None) -> str:
    base = f"/v1/cases/{case}/support-access"
    return base if grant is None else f"{base}/{grant}"


@pytest.fixture
def staff(world: World) -> uuid.UUID:
    person = world.seed.person(full_name="Support")
    world.seed.support_staff(person)
    return person


async def test_s17_the_named_expert_grants_24_hours_and_support_can_look(
    client_for: ClientFor, world: World, staff: uuid.UUID, app: FastAPI, admin: psycopg.Connection
) -> None:
    case = world.team_case()
    before = await (await client_for(staff)).get(f"/v1/cases/{case}")
    assert before.status_code == 404

    resp = await (await client_for(world["dr_k"])).post(url(case), json={"reason": "upload_stuck"})
    assert resp.status_code == 201, resp.text
    grant = resp.json()
    assert (grant["case_id"], grant["reason"], grant["granted_to"], grant["active"]) == (
        str(case), "upload_stuck", None, True,
    )  # fmt: skip
    expires = datetime.fromisoformat(grant["expires_at"])
    assert timedelta(hours=23, minutes=59) <= expires - datetime.now(UTC) <= timedelta(hours=24)
    [mail] = [m for m in app.state.mailer.outbox if m.template == SUPPORT_ACCESS_GRANTED]
    assert mail.to == world.email("dr_k") and mail.params["grant_ref"] == grant["id"]

    looked = await (await client_for(staff)).get(f"/v1/cases/{case}")
    assert looked.status_code == 200, looked.text
    trail = (await (await client_for(world["dr_k"])).get(f"/v1/cases/{case}/audit")).json()["entries"]
    actions = [(e["action"], e["person_id"]) for e in trail]
    assert ("support_access.granted", str(world["dr_k"])) in actions
    assert ("support_access.viewed", str(staff)) in actions


async def test_a_grant_for_one_staff_member_only(client_for: ClientFor, world: World, staff: uuid.UUID) -> None:
    other = world.seed.person()
    world.seed.support_staff(other)
    case = world.team_case()
    resp = await (await client_for(world["dr_k"])).post(
        url(case), json={"reason": "upload_stuck", "granted_to": str(staff)}
    )
    assert resp.status_code == 201, resp.text
    assert (await (await client_for(staff)).get(f"/v1/cases/{case}")).status_code == 200
    assert (await (await client_for(other)).get(f"/v1/cases/{case}")).status_code == 404


async def test_granted_to_must_be_current_staff(client_for: ClientFor, world: World) -> None:
    case = world.team_case()
    for person in (world["dr_m"], uuid.uuid4()):
        resp = await (await client_for(world["dr_k"])).post(
            url(case), json={"reason": "upload_stuck", "granted_to": str(person)}
        )
        assert resp.status_code == 422


@pytest.mark.parametrize("reason", ["Free text reason", "", "x" * 41, "DROP"])
async def test_the_reason_is_a_short_code(client_for: ClientFor, world: World, reason: str) -> None:
    resp = await (await client_for(world["dr_k"])).post(url(world.team_case()), json={"reason": reason})
    assert resp.status_code == 422


async def test_a_new_grant_ends_the_earlier_one(client_for: ClientFor, world: World, admin: psycopg.Connection) -> None:
    case = world.team_case()
    client = await client_for(world["dr_k"])
    first = (await client.post(url(case), json={"reason": "upload_stuck"})).json()
    second = (await client.post(url(case), json={"reason": "page_missing"})).json()
    listed = {g["id"]: g["active"] for g in (await client.get(url(case))).json()["grants"]}
    assert listed == {first["id"]: False, second["id"]: True}


async def test_revoking_ends_access_at_once_and_for_good(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    case = world.team_case()
    client = await client_for(world["dr_k"])
    grant = (await client.post(url(case), json={"reason": "upload_stuck"})).json()
    resp = await client.delete(url(case, grant["id"]))
    assert resp.status_code == 204, resp.text
    assert (await (await client_for(staff)).get(f"/v1/cases/{case}")).status_code == 404
    assert (await client.delete(url(case, grant["id"]))).status_code == 409
    assert (await client.delete(url(case, uuid.uuid4()))).status_code == 404
    assert admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'support_access.revoked'", (case,)
    ).fetchone() == (1,)


async def test_f38_an_expired_grant_shows_as_ended_and_opens_nothing(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    case = world.team_case()
    world.seed.support_grant(case, world["dr_k"], expires_in=timedelta(seconds=-1))
    listed = (await (await client_for(world["owner"])).get(url(case))).json()["grants"]
    assert [g["active"] for g in listed] == [False]
    assert (await (await client_for(staff)).get(f"/v1/cases/{case}")).status_code == 404


async def test_a_grant_from_the_previous_named_expert_shows_as_ended_after_reassignment(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    case = world.team_case()
    given = await (await client_for(world["dr_k"])).post(url(case), json={"reason": "upload_stuck"})
    assert given.status_code == 201, given.text
    moved = await (await client_for(world["owner"])).post(
        f"/v1/cases/{case}/named-expert", json={"person_id": str(world["dr_m"])}
    )
    assert moved.status_code == 200, moved.text
    for who in ("dr_m", "owner"):
        listed = (await (await client_for(world[who])).get(url(case))).json()["grants"]
        assert [g["active"] for g in listed] == [False], who
    assert (await (await client_for(staff)).get(f"/v1/cases/{case}")).status_code == 404


async def test_grants_given_at_the_same_time_leave_one_active(
    api_db: Database, world: World, admin: psycopg.Connection
) -> None:
    """The case row is locked before the earlier grant is ended, so two at once cannot both stay active."""
    for _ in range(5):
        case = world.team_case()

        async def give(reason: str, case: uuid.UUID = case) -> None:
            async with api_db.request(scope_for(world["dr_k"])) as conn:
                await support.give(conn, OutboxMailer(), case, SupportGrantCreate(reason=reason), world.email("dr_k"))

        await asyncio.gather(give("upload_stuck"), give("page_missing"))
        active = admin.execute(
            "select count(*) from app.support_access_grants"
            " where case_id = %s and revoked_at is null and expires_at > now()",
            (case,),
        ).fetchone()
        assert active == (1,)


async def test_giving_a_grant_locks_the_case_row(api_db: Database, world: World, admin: psycopg.Connection) -> None:
    case = world.team_case()
    async with api_db.request(scope_for(world["dr_k"])) as conn:
        await support.give(conn, OutboxMailer(), case, SupportGrantCreate(reason="upload_stuck"), world.email("dr_k"))
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with psycopg.connect(admin.info.dsn, password="postgres", autocommit=True) as other:
                other.execute("set lock_timeout = '200ms'")
                other.execute("select id from app.cases where id = %s for no key update", (case,))


@pytest.mark.parametrize(("who", "status"), [("owner", 403), ("assistant", 403), ("outsider", 404)])
async def test_only_the_named_expert_grants(client_for: ClientFor, world: World, who: str, status: int) -> None:
    case = world.team_case(members={"assistant": "assistant"})
    resp = await (await client_for(world[who])).post(url(case), json={"reason": "upload_stuck"})
    assert resp.status_code == status


async def test_the_grant_shows_on_the_case_to_those_who_see_it(client_for: ClientFor, world: World) -> None:
    case = world.team_case(members={"viewer": "viewer"})
    world.seed.support_grant(case, world["dr_k"])
    for who in ("dr_k", "viewer", "owner"):
        resp = await (await client_for(world[who])).get(url(case))
        assert resp.status_code == 200, who
        assert len(resp.json()["grants"]) == 1
    assert (await (await client_for(world["outsider"])).get(url(case))).status_code == 404


async def test_granting_and_revoking_need_step_up(client_for: ClientFor, world: World) -> None:
    case = world.team_case()
    gid = world.seed.support_grant(case, world["dr_k"])
    client = await client_for(world["dr_k"], mfa_age=STALE)
    for resp in (await client.post(url(case), json={"reason": "upload_stuck"}), await client.delete(url(case, gid))):
        assert (resp.status_code, resp.json()["error"]["code"]) == (403, "step_up_required")
