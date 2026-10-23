# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""What the web screens ask the server instead of working out (rule 4; reviewer findings on #65).

- Member case counts are shown to owners and admins only (`org.view_all_cases`); everyone else gets null.
  Cases deleted or being deleted are not counted.
- `GET /v1/organisations/{org_id}` says what the person may do there (`allowed_actions`) and which roles
  they may give (`assignable_roles`).
- `GET /v1/cases/{case_id}/named-expert/candidates` (`case.reassign_named_expert`): who may become the named
  expert, and whether the case was served.
- `GET /v1/me/close` lists the cases that closing the account would delete, with their export status.
"""

from __future__ import annotations

import pytest

from tests.fixtures.test_organisations_world import World, world  # noqa: F401
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


async def counts(client_for: ClientFor, w: World, who: str) -> dict[str, int | None]:
    resp = await (await client_for(w[who])).get(f"/v1/organisations/{w.agency}/members")
    assert resp.status_code == 200, resp.text
    return {m["person_id"]: m["case_count"] for m in resp.json()["members"]}


async def test_owners_and_admins_see_each_members_case_count(client_for: ClientFor, world: World) -> None:
    world.team_case("dr_k")
    world.team_case("dr_k", status="served")
    world.team_case("dr_m")
    world.solo_case()  # another organisation's case: not counted here
    for who in ("owner", "admin"):
        found = await counts(client_for, world, who)
        assert (found[str(world["dr_k"])], found[str(world["dr_m"])], found[str(world["owner"])]) == (2, 1, 0)


@pytest.mark.parametrize("who", ["dr_k", "assistant", "viewer"])
async def test_other_members_get_no_case_counts(client_for: ClientFor, world: World, who: str) -> None:
    world.team_case("dr_k")
    found = await counts(client_for, world, who)
    assert set(found.values()) == {None}


async def test_deleted_and_being_deleted_cases_are_not_counted(client_for: ClientFor, world: World) -> None:
    world.team_case("dr_k")
    world.team_case("dr_k", status="deleted")
    going = world.team_case("dr_k")
    world.admin.execute("update app.cases set retention_policy = 'delete_now' where id = %s", (going,))
    found = await counts(client_for, world, "owner")
    assert found[str(world["dr_k"])] == 1


async def test_the_organisation_says_what_the_person_may_do_and_which_roles_they_may_give(
    client_for: ClientFor, world: World
) -> None:
    owner = (await (await client_for(world["owner"])).get(f"/v1/organisations/{world.agency}")).json()
    admin = (await (await client_for(world["admin"])).get(f"/v1/organisations/{world.agency}")).json()
    viewer = (await (await client_for(world["viewer"])).get(f"/v1/organisations/{world.agency}")).json()
    assert "org.manage_members" in owner["allowed_actions"] and "org.close" in owner["allowed_actions"]
    assert "owner" in owner["assignable_roles"]
    assert "org.manage_members" in admin["allowed_actions"] and "org.close" not in admin["allowed_actions"]
    assert "owner" not in admin["assignable_roles"] and "expert" in admin["assignable_roles"]
    assert "org.manage_members" not in viewer["allowed_actions"] and viewer["assignable_roles"] == []


async def test_candidates_for_named_expert_are_current_experts_owners_and_admins_but_not_the_current_one(
    client_for: ClientFor, world: World
) -> None:
    case = world.team_case("dr_k")
    resp = await (await client_for(world["admin"])).get(f"/v1/cases/{case}/named-expert/candidates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["served"] is False
    ids = {c["person_id"] for c in body["candidates"]}
    assert ids == {str(world[n]) for n in ("owner", "admin", "dr_m")}
    assert all(c["full_name"] and c["role"] in ("owner", "admin", "expert") for c in body["candidates"])


async def test_candidates_say_when_the_case_was_served(client_for: ClientFor, world: World) -> None:
    case = world.team_case("dr_k", status="served")
    body = (await (await client_for(world["owner"])).get(f"/v1/cases/{case}/named-expert/candidates")).json()
    assert body["served"] is True


@pytest.mark.parametrize("who", ["dr_k", "dr_m", "assistant", "viewer"])
async def test_only_owners_and_admins_list_candidates(client_for: ClientFor, world: World, who: str) -> None:
    case = world.team_case("dr_k")
    resp = await (await client_for(world[who])).get(f"/v1/cases/{case}/named-expert/candidates")
    assert resp.status_code in (403, 404)


async def test_a_stranger_gets_not_found_for_candidates(client_for: ClientFor, world: World) -> None:
    case = world.team_case("dr_k")
    resp = await (await client_for(world["outsider"])).get(f"/v1/cases/{case}/named-expert/candidates")
    assert resp.status_code == 404


async def test_closing_the_account_lists_unserved_own_cases_with_their_export_status(
    client_for: ClientFor, world: World
) -> None:
    mine = world.solo_case()
    world.solo_case(status="served")  # kept, so not listed
    world.team_case("dr_k")  # a team's case: not deleted by closing the account
    done = world.solo_case()
    job = world.seed.job(done, kind="export_case", requested_by=world["dr_k"])
    for step in ("running", "done"):
        world.admin.execute("update app.jobs set status = %s where id = %s", (step, job))
    resp = await (await client_for(world["dr_k"])).get("/v1/me/close")
    assert resp.status_code == 200, resp.text
    cases = {c["case_id"]: c for c in resp.json()["cases"]}
    assert set(cases) == {str(mine), str(done)}
    assert cases[str(mine)]["exported"] is False and cases[str(done)]["exported"] is True
    assert cases[str(mine)]["label"]
