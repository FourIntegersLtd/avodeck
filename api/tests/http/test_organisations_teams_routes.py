"""Teams (architecture 5.4 "Organisations and accounts", 10.1 "Organisations").

- `GET /v1/organisations`: the organisations I currently belong to, with my role.
- `POST /v1/organisations`: create a team; the creator becomes its owner, in one step (migration 1000).
- `GET /v1/organisations/{org_id}`: a current member sees it; anyone else, or a member who left, gets 404.
- `PATCH /v1/organisations/{org_id}`: owner or admin renames it (403 for other members, 404 for non-members);
  needs step-up like the organisation's other settings.
- Row-level security repeats it: the running app cannot insert organisations or memberships directly.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio


async def test_lists_my_current_organisations_with_my_role(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["dr_k"])).get("/v1/organisations")
    assert resp.status_code == 200, resp.text
    got = {(o["name"], o["kind"], o["role"]) for o in resp.json()["organisations"]}
    assert got == {("Solo practice", "personal", "owner"), ("Agency", "team", "expert")}


async def test_a_left_organisation_is_not_listed(client_for: ClientFor, world: World) -> None:
    world.seed.end_membership(world.agency, world["viewer"])
    resp = await (await client_for(world["viewer"])).get("/v1/organisations")
    assert [o["kind"] for o in resp.json()["organisations"]] == ["personal"]


async def test_creating_a_team_makes_the_creator_its_owner(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    client = await client_for(world["outsider"])
    resp = await client.post("/v1/organisations", json={"name": "Chambers"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["name"], body["kind"], body["role"], body["region"]) == ("Chambers", "team", "owner", "zz")
    org = uuid.UUID(body["id"])
    assert admin.execute(
        "select person_id, role from app.memberships where organisation_id = %s and ended_at is null", (org,)
    ).fetchall() == [(world["outsider"], "owner")]
    assert admin.execute(
        "select count(*) from app.audit_log where organisation_id = %s and action = 'organisation.created'", (org,)
    ).fetchone() == (1,)
    listed = (await client.get("/v1/organisations")).json()["organisations"]
    assert str(org) in {o["id"] for o in listed}


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
async def test_a_team_needs_a_sensible_name(client_for: ClientFor, world: World, name: str) -> None:
    resp = await (await client_for(world["outsider"])).post("/v1/organisations", json={"name": name})
    assert resp.status_code == 422


async def test_creating_a_team_needs_csrf(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["outsider"], csrf=False)).post("/v1/organisations", json={"name": "T"})
    assert resp.status_code == 403


async def test_a_member_sees_the_organisation(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["viewer"])).get(f"/v1/organisations/{world.agency}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["id"], body["name"], body["kind"], body["role"]) == (str(world.agency), "Agency", "team", "viewer")
    assert body["policy"] == {"required_template_id": None, "max_retention_years": None}


@pytest.mark.parametrize("who", ["outsider", "left"])
async def test_anyone_else_gets_404(client_for: ClientFor, world: World, who: str) -> None:
    person = world["outsider"]
    if who == "left":
        person = world["viewer"]
        world.seed.end_membership(world.agency, person)
    resp = await (await client_for(person)).get(f"/v1/organisations/{world.agency}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    missing = await (await client_for(world["owner"])).get(f"/v1/organisations/{uuid.uuid4()}")
    assert missing.status_code == 404


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_owner_or_admin_renames_it(
    client_for: ClientFor, world: World, admin: psycopg.Connection, role: str
) -> None:
    resp = await (await client_for(world[role])).patch(f"/v1/organisations/{world.agency}", json={"name": "Agency Two"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Agency Two"
    assert admin.execute("select name from app.organisations where id = %s", (world.agency,)).fetchone() == (
        "Agency Two",
    )
    assert admin.execute(
        "select count(*) from app.audit_log where organisation_id = %s and action = 'organisation.renamed'",
        (world.agency,),
    ).fetchone() == (1,)


@pytest.mark.parametrize("role", ["dr_k", "assistant", "viewer"])
async def test_other_members_cannot_rename(client_for: ClientFor, world: World, role: str) -> None:
    resp = await (await client_for(world[role])).patch(f"/v1/organisations/{world.agency}", json={"name": "Mine"})
    assert resp.status_code == 403


async def test_renaming_needs_step_up_and_hides_from_non_members(client_for: ClientFor, world: World) -> None:
    stale = await (await client_for(world["owner"], mfa_age=STALE)).patch(
        f"/v1/organisations/{world.agency}", json={"name": "X"}
    )
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "step_up_required")
    outsider = await (await client_for(world["outsider"])).patch(f"/v1/organisations/{world.agency}", json={"name": "X"})
    assert outsider.status_code == 404


async def test_the_running_app_cannot_insert_organisations_or_memberships_directly(
    api_db: Database, world: World
) -> None:
    async with api_db.request(scope_for(world["outsider"])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("insert into app.organisations (name, kind, region) values ('T', 'team', 'zz')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute(
                    "insert into app.memberships (organisation_id, person_id, role) values (%s, %s, 'owner')",
                    (world.agency, world["outsider"]),
                )


async def test_the_team_function_creates_only_for_the_signed_in_person(api_db: Database, world: World) -> None:
    async with api_db.request(scope_for(world["dr_m"])) as conn:
        cur = await conn.execute("select app.create_team_organisation('Group', 'zz')")
        row = await cur.fetchone()
        assert row is not None
        cur = await conn.execute(
            "select person_id, role from app.memberships where organisation_id = %s", (row[0],)
        )
        assert await cur.fetchall() == [(world["dr_m"], "owner")]
