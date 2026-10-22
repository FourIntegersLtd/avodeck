# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Slice O's scenarios through the API, on the fixture packs, with "Solo practice" and "Agency"
(docs/spec/scenarios.md).

- S1: one sign-in; Today shows both organisations' cases, each with its organisation badge, and filters by
  organisation. (The conflict check across both organisations is slice C's: test_setup_conflicts_other_cases.py.
  Separate invoice numbering: test_organisations_billing_routes.py.)
- permissions.md "second case": the same person, named expert on a personal case and assistant on a team case,
  has the right rights on each.
- Owners and admins see every organisation case's metadata, never its pages unless added to the case.
S2 and F31: test_organisations_leave_routes.py. S3: test_organisations_reassign_routes.py. F32:
test_organisations_members_routes.py and test_organisations_leave_routes.py.
"""

from __future__ import annotations

import pytest

from app.core.auth.access import check_case_access
from app.core.auth.permissions import allowed_actions
from app.core.db import Database
from app.core.errors import ApiError
from tests.fixtures.test_organisations_world import World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio


async def test_s1_today_shows_both_organisations_with_badges_and_a_filter(client_for: ClientFor, world: World) -> None:
    solo = world.solo_case()
    agency = world.team_case()
    world.team_case("dr_m")  # someone else's agency case: not hers
    client = await client_for(world["dr_k"])
    today = (await client.get("/v1/today")).json()
    cards = {c["case_id"]: (c["organisation_name"], c["organisation_kind"]) for c in today["cases"]}
    assert cards == {str(solo): ("Solo practice", "personal"), str(agency): ("Agency", "team")}
    assert {(o["name"], o["kind"]) for o in today["organisations"]} == {
        ("Solo practice", "personal"),
        ("Agency", "team"),
    }
    only_agency = (await client.get("/v1/today", params={"organisation_id": str(world.agency)})).json()
    assert [c["case_id"] for c in only_agency["cases"]] == [str(agency)]


async def test_second_case_named_on_personal_assistant_on_team(world: World, api_db: Database) -> None:
    personal = world.solo_case()
    helping = world.team_case("dr_m", members={"dr_k": "assistant"})
    async with api_db.request(scope_for(world["dr_k"])) as conn:
        mine = await check_case_access(conn, personal, "case.view_content")
        assert set(mine.allowed_actions) == set(allowed_actions("named_expert", "owner", "personal"))
        assert "report.sign" in mine.allowed_actions
        theirs = await check_case_access(conn, helping, "case.view_content")
        assert set(theirs.allowed_actions) == set(allowed_actions("assistant", "expert", "team"))
        for action in ("report.sign", "fact.verify", "opinion.write", "report.serve"):
            assert action not in theirs.allowed_actions
            with pytest.raises(ApiError) as err:
                await check_case_access(conn, helping, action)
            assert err.value.status == 403


@pytest.mark.parametrize("who", ["owner", "admin"])
async def test_owners_and_admins_see_metadata_of_every_case_never_pages(
    world: World, api_db: Database, client_for: ClientFor, who: str
) -> None:
    case = world.team_case("dr_m")
    async with api_db.request(scope_for(world[who])) as conn:
        access = await check_case_access(conn, case, "case.view_metadata")
        assert access.case_role is None and access.org_role == who
        with pytest.raises(ApiError) as err:
            await check_case_access(conn, case, "case.view_content")
        assert err.value.status == 403
    world.seed.case_member(case, world[who], "viewer", added_by=world["dr_m"])
    async with api_db.request(scope_for(world[who])) as conn:
        assert (await check_case_access(conn, case, "case.view_content")).case_role == "viewer"
    listed = (
        await (await client_for(world[who])).get("/v1/today", params={"organisation_id": str(world.agency)})
    ).json()
    assert str(case) in {c["case_id"] for c in listed["cases"]}


@pytest.mark.parametrize("who", ["dr_k", "assistant", "viewer"])
async def test_other_members_see_only_their_own_and_assigned_cases(world: World, api_db: Database, who: str) -> None:
    case = world.team_case("dr_m")
    async with api_db.request(scope_for(world[who])) as conn:
        with pytest.raises(ApiError) as err:
            await check_case_access(conn, case, "case.view_metadata")
        assert err.value.status == 404
