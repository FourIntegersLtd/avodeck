"""Members (architecture 5.4, 10.1; permissions.md "Organisation actions"; state-machines.md Membership; F32).

- `GET /v1/organisations/{org_id}/members`: every member sees members with role and dates (`org.view_members`).
- `PATCH /v1/organisations/{org_id}/members/{membership_id}` 🔒: owner or admin changes a role; an admin
  cannot change an owner or make anyone an owner (403).
- `DELETE /v1/organisations/{org_id}/members/{membership_id}` 🔒: ends the membership (history kept).
- F32: the last owner cannot be removed or demoted: 409 `last_owner`, "Make someone else an owner first."
- A person's membership of their personal organisation never ends while the account is open (409).
- Row-level security and triggers repeat it (migration 1001).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio


def members_url(w: World, name: str | None = None, org: uuid.UUID | None = None) -> str:
    base = f"/v1/organisations/{org or w.agency}/members"
    return base if name is None else f"{base}/{w.memberships[name]}"


async def test_every_member_sees_members_with_role_and_dates(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["viewer"])).get(members_url(world))
    assert resp.status_code == 200, resp.text
    members = {m["person_id"]: m for m in resp.json()["members"]}
    assert set(members) == {str(world[n]) for n in ("owner", "admin", "dr_k", "dr_m", "assistant", "viewer")}
    k = members[str(world["dr_k"])]
    assert (k["membership_id"], k["role"], k["full_name"], k["email"], k["ended_at"]) == (
        str(world.memberships["dr_k"]), "expert", "Dr K", world.email("dr_k"), None,
    )  # fmt: skip
    assert k["started_at"]


async def test_members_list_keeps_people_who_left_with_the_date(client_for: ClientFor, world: World) -> None:
    world.seed.end_membership(world.agency, world["viewer"])
    resp = await (await client_for(world["owner"])).get(members_url(world))
    left = [m for m in resp.json()["members"] if m["person_id"] == str(world["viewer"])]
    assert len(left) == 1 and left[0]["ended_at"] is not None


async def test_non_members_get_404(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["outsider"])).get(members_url(world))
    assert resp.status_code == 404


@pytest.mark.parametrize("who", ["owner", "admin"])
async def test_owner_or_admin_changes_a_role(
    client_for: ClientFor, world: World, admin: psycopg.Connection, who: str
) -> None:
    resp = await (await client_for(world[who])).patch(members_url(world, "assistant"), json={"role": "viewer"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "viewer"
    assert world.membership_row(world.agency, "assistant") == ("viewer", False)
    assert admin.execute(
        "select count(*) from app.audit_log where organisation_id = %s and action = 'membership.role_changed'"
        " and target = %s",
        (world.agency, f"membership:{world.memberships['assistant']}"),
    ).fetchone() == (1,)


async def test_owner_makes_another_owner(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["owner"])).patch(members_url(world, "admin"), json={"role": "owner"})
    assert resp.status_code == 200, resp.text
    assert world.membership_row(world.agency, "admin") == ("owner", False)


@pytest.mark.parametrize(("target", "role"), [("owner", "admin"), ("dr_m", "owner")])
async def test_an_admin_cannot_change_an_owner_or_make_one(
    client_for: ClientFor, world: World, target: str, role: str
) -> None:
    resp = await (await client_for(world["admin"])).patch(members_url(world, target), json={"role": role})
    assert resp.status_code == 403
    assert world.membership_row(world.agency, "owner") == ("owner", False)
    assert world.membership_row(world.agency, "dr_m") == ("expert", False)


async def test_an_admin_cannot_remove_an_owner(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["admin"])).delete(members_url(world, "owner"))
    assert resp.status_code == 403
    assert world.membership_row(world.agency, "owner") == ("owner", False)


@pytest.mark.parametrize("who", ["dr_k", "assistant", "viewer"])
async def test_other_members_cannot_manage_members(client_for: ClientFor, world: World, who: str) -> None:
    client = await client_for(world[who])
    assert (await client.patch(members_url(world, "dr_m"), json={"role": "viewer"})).status_code == 403
    assert (await client.delete(members_url(world, "dr_m"))).status_code == 403


async def test_removing_ends_the_membership_and_keeps_history(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    resp = await (await client_for(world["admin"])).delete(members_url(world, "dr_m"))
    assert resp.status_code == 204, resp.text
    assert world.membership_row(world.agency, "dr_m") == ("expert", True)
    assert admin.execute(
        "select count(*) from app.audit_log where organisation_id = %s and action = 'membership.ended'",
        (world.agency,),
    ).fetchone() == (1,)
    again = await (await client_for(world["admin"])).delete(members_url(world, "dr_m"))
    assert again.status_code == 404


async def test_a_membership_of_another_organisation_is_404(client_for: ClientFor, world: World) -> None:
    other = world.seed.org(kind="team", members={world["owner"]: "owner"})
    stranger = world.seed.membership(other, world["outsider"], "viewer")
    resp = await (await client_for(world["owner"])).delete(f"/v1/organisations/{world.agency}/members/{stranger}")
    assert resp.status_code == 404
    unknown = await (await client_for(world["owner"])).delete(f"/v1/organisations/{world.agency}/members/{uuid.uuid4()}")
    assert unknown.status_code == 404


@pytest.mark.parametrize("change", ["demote", "remove"])
async def test_f32_the_last_owner_cannot_be_demoted_or_removed(
    client_for: ClientFor, world: World, change: str
) -> None:
    client = await client_for(world["owner"])
    if change == "demote":
        resp = await client.patch(members_url(world, "owner"), json={"role": "admin"})
    else:
        resp = await client.delete(members_url(world, "owner"))
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == {"code": "last_owner", "message": "Make someone else an owner first."}
    assert world.membership_row(world.agency, "owner") == ("owner", False)


async def test_with_a_second_owner_the_first_can_be_removed(client_for: ClientFor, world: World) -> None:
    client = await client_for(world["owner"])
    assert (await client.patch(members_url(world, "admin"), json={"role": "owner"})).status_code == 200
    assert (await client.delete(members_url(world, "owner"))).status_code == 204


async def test_a_personal_organisation_membership_never_ends_while_the_account_is_open(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    [mid] = admin.execute(
        "select id from app.memberships where organisation_id = %s and person_id = %s", (world.solo, world["dr_k"])
    ).fetchone() or (None,)
    resp = await (await client_for(world["dr_k"])).delete(f"/v1/organisations/{world.solo}/members/{mid}")
    assert resp.status_code == 409
    assert world.membership_row(world.solo, "dr_k") == ("owner", False)


async def test_member_changes_need_step_up(client_for: ClientFor, world: World) -> None:
    client = await client_for(world["owner"], mfa_age=STALE)
    for resp in (
        await client.patch(members_url(world, "dr_m"), json={"role": "viewer"}),
        await client.delete(members_url(world, "dr_m")),
    ):
        assert (resp.status_code, resp.json()["error"]["code"]) == (403, "step_up_required")


@pytest.mark.parametrize("role", ["superuser", "", None])
async def test_only_known_roles(client_for: ClientFor, world: World, role: str | None) -> None:
    resp = await (await client_for(world["owner"])).patch(members_url(world, "dr_m"), json={"role": role})
    assert resp.status_code == 422


# --- the database repeats it --------------------------------------------------------------------------


async def _try(conn: psycopg.AsyncConnection, sql: str, params: tuple[object, ...]) -> int:
    async with conn.transaction():
        cur = await conn.execute(sql, params)
        return cur.rowcount


async def test_an_admin_cannot_make_an_owner_even_directly(api_db: Database, world: World) -> None:
    async with api_db.request(scope_for(world["admin"])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await _try(conn, "update app.memberships set role = 'owner' where id = %s", (world.memberships["dr_m"],))


async def test_a_plain_member_changes_nobody_else_directly(api_db: Database, world: World) -> None:
    async with api_db.request(scope_for(world["viewer"])) as conn:
        changed = await _try(
            conn, "update app.memberships set role = 'viewer' where id = %s", (world.memberships["dr_m"],)
        )
        assert changed == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await _try(conn, "update app.memberships set role = 'owner' where id = %s", (world.memberships["viewer"],))


async def test_the_member_list_function_answers_only_members(api_db: Database, world: World) -> None:
    async with api_db.request(scope_for(world["outsider"])) as conn:
        cur = await conn.execute("select count(*) from app.organisation_members(%s)", (world.agency,))
        assert await cur.fetchone() == (0,)
