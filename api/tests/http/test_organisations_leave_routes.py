"""Leaving an organisation (architecture 10.1 "Leaving", scenario S2, F31, F32; permissions.md "left the organisation").

`POST /v1/organisations/{org_id}/leave` ends the person's own membership (`ended_at`, history kept).
- F31 / S2: the named expert keeps every named-expert action on every case they are named on, including
  court-stage work, served or not; their case-member (e.g. assistant) rights on the organisation's other
  cases end; their Today shows only their named cases from that organisation. The organisation still sees
  the named cases' metadata (administrative copy), never their content.
- F32: the last owner cannot leave (409 `last_owner`, "Make someone else an owner first.").
- A personal organisation membership never ends while the account is open (409).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.auth.access import check_case_access
from app.core.auth.permissions import allowed_actions
from app.core.db import Database
from app.core.errors import ApiError
from tests.fixtures.test_organisations_world import World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio

COURT_STAGE = ("report.serve", "report.withdraw", "request.send", "case.withdraw", "case.export", "fact.verify",
               "opinion.write", "invoice.send", "legal_hold.set", "support_access.grant")  # fmt: skip


def leave_url(w: World, org: uuid.UUID | None = None) -> str:
    return f"/v1/organisations/{org or w.agency}/leave"


async def test_leaving_ends_my_membership_and_keeps_history(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    resp = await (await client_for(world["dr_m"])).post(leave_url(world))
    assert resp.status_code == 204, resp.text
    assert world.membership_row(world.agency, "dr_m") == ("expert", True)
    assert admin.execute(
        "select count(*) from app.audit_log where organisation_id = %s and action = 'membership.left'"
        " and person_id = %s",
        (world.agency, world["dr_m"]),
    ).fetchone() == (1,)
    after = await (await client_for(world["dr_m"])).get(f"/v1/organisations/{world.agency}")
    assert after.status_code == 404


async def test_f31_s2_named_cases_keep_every_named_expert_action(
    client_for: ClientFor, world: World, api_db: Database
) -> None:
    served = world.team_case(status="served")
    unserved = world.team_case(status="active")
    helping = world.team_case("dr_m", members={"dr_k": "assistant"})
    others = world.team_case("dr_m")
    solo = world.solo_case()
    resp = await (await client_for(world["dr_k"])).post(leave_url(world))
    assert resp.status_code == 204, resp.text

    named = set(allowed_actions("named_expert", None, None))
    async with api_db.request(scope_for(world["dr_k"])) as conn:
        for case in (served, unserved):
            access = await check_case_access(conn, case, "case.view_content")
            assert set(access.allowed_actions) == named
            assert set(COURT_STAGE) <= set(access.allowed_actions)
        for case in (helping, others):
            with pytest.raises(ApiError) as err:
                await check_case_access(conn, case, "case.view_metadata")
            assert err.value.status == 404

    today = (await (await client_for(world["dr_k"])).get("/v1/today")).json()
    shown = {c["case_id"] for c in today["cases"]}
    assert {str(served), str(unserved), str(solo)} <= shown
    assert str(helping) not in shown and str(others) not in shown
    from_agency = {c["case_id"] for c in today["cases"] if c["organisation_id"] == str(world.agency)}
    assert from_agency == {str(served), str(unserved)}


async def test_s2_the_organisation_keeps_metadata_but_not_content(world: World, api_db: Database, client_for: ClientFor) -> None:
    case = world.team_case(status="served")
    assert (await (await client_for(world["dr_k"])).post(leave_url(world))).status_code == 204
    async with api_db.request(scope_for(world["owner"])) as conn:
        access = await check_case_access(conn, case, "case.view_metadata")
        assert "case.view_content" not in access.allowed_actions
        with pytest.raises(ApiError) as err:
            await check_case_access(conn, case, "case.view_content")
        assert err.value.status == 403


async def test_f32_the_last_owner_cannot_leave(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["owner"])).post(leave_url(world))
    assert resp.status_code == 409
    assert resp.json()["error"] == {"code": "last_owner", "message": "Make someone else an owner first."}
    assert world.membership_row(world.agency, "owner") == ("owner", False)


async def test_an_owner_can_leave_once_there_is_another(client_for: ClientFor, world: World) -> None:
    owner = await client_for(world["owner"])
    assert (await owner.patch(f"/v1/organisations/{world.agency}/members/{world.memberships['admin']}",
                              json={"role": "owner"})).status_code == 200  # fmt: skip
    assert (await owner.post(leave_url(world))).status_code == 204


async def test_nobody_leaves_their_personal_organisation(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["dr_k"])).post(leave_url(world, world.solo))
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "personal_organisation")


async def test_non_members_get_404(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["outsider"])).post(leave_url(world))
    assert resp.status_code == 404


async def test_left_the_organisation_assistant_rights_go_named_rights_stay(world: World, api_db: Database) -> None:
    """permissions.md "Tests": the same person, named on one case and assistant on another, leaves."""
    named = world.team_case("dr_m")
    helping = world.team_case("dr_k", members={"dr_m": "assistant"})
    world.seed.end_membership(world.agency, world["dr_m"])
    async with api_db.request(scope_for(world["dr_m"])) as conn:
        assert "report.sign" in (await check_case_access(conn, named, "report.sign")).allowed_actions
        with pytest.raises(ApiError) as err:
            await check_case_access(conn, helping, "case.upload")
        assert err.value.status == 404
