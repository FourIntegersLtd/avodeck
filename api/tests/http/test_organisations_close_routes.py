"""Closing an organisation (architecture 5.4, 10.1 "Organisation closure"; state-machines.md Membership).

`POST /v1/organisations/{org_id}/close` 🔒, owner only (`org.close`):
- refused (409 `cases_open`) while any of the organisation's cases is open and has never been served
  (draft, processing, active); finished, withdrawn, deleted and served cases do not stop it;
- closes the organisation, ends every membership (owners too) and cancels pending invites;
- served cases survive until their retention ends, and their named experts keep access to them;
- a personal organisation is closed only with the account (409 here).
"""

from __future__ import annotations

import pytest
import psycopg

from app.core.auth.access import check_case_access
from app.core.db import Database
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio


def url(w: World) -> str:
    return f"/v1/organisations/{w.agency}/close"


@pytest.mark.parametrize("status", ["draft", "processing", "active"])
async def test_refused_while_an_unserved_case_is_open(
    client_for: ClientFor, world: World, admin: psycopg.Connection, status: str
) -> None:
    world.team_case(status="served")
    world.team_case(status=status)
    resp = await (await client_for(world["owner"])).post(url(world))
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "cases_open")
    assert admin.execute("select closed_at from app.organisations where id = %s", (world.agency,)).fetchone() == (None,)
    assert world.membership_row(world.agency, "owner") == ("owner", False)


async def test_closing_ends_every_membership_and_keeps_served_cases(
    client_for: ClientFor, world: World, admin: psycopg.Connection, api_db: Database
) -> None:
    served = world.team_case(status="served", members={"assistant": "assistant"})
    for status in ("settled", "withdrawn", "deleted"):
        world.team_case(status=status)
    admin.execute(
        "insert into app.invites (organisation_id, email, role, invited_by, token_hash)"
        " values (%s, 'later@example.test', 'viewer', %s, %s)",
        (world.agency, world["owner"], world.agency.hex * 2),
    )
    resp = await (await client_for(world["owner"])).post(url(world))
    assert resp.status_code == 204, resp.text

    assert admin.execute(
        "select closed_at is not null from app.organisations where id = %s", (world.agency,)
    ).fetchone() == (True,)
    assert admin.execute(
        "select count(*) from app.memberships where organisation_id = %s and ended_at is null", (world.agency,)
    ).fetchone() == (0,)
    assert admin.execute(
        "select status from app.invites where organisation_id = %s", (world.agency,)
    ).fetchall() == [("cancelled",)]
    assert admin.execute("select status from app.cases where id = %s", (served,)).fetchone() == ("served",)
    assert admin.execute(
        "select count(*) from app.audit_log where organisation_id = %s and action = 'organisation.closed'",
        (world.agency,),
    ).fetchone() == (1,)

    async with api_db.request(scope_for(world["dr_k"])) as conn:
        access = await check_case_access(conn, served, "case.view_content")
        assert {"report.withdraw", "case.export", "case.view_audit"} <= set(access.allowed_actions)
    gone = await (await client_for(world["owner"])).get(f"/v1/organisations/{world.agency}")
    assert gone.status_code == 404
    listed = (await (await client_for(world["dr_k"])).get("/v1/organisations")).json()["organisations"]
    assert str(world.agency) not in {o["id"] for o in listed}


@pytest.mark.parametrize(("who", "status"), [("admin", 403), ("dr_k", 403), ("viewer", 403), ("outsider", 404)])
async def test_only_an_owner_closes(client_for: ClientFor, world: World, who: str, status: int) -> None:
    resp = await (await client_for(world[who])).post(url(world))
    assert resp.status_code == status


async def test_closing_needs_step_up(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["owner"], mfa_age=STALE)).post(url(world))
    assert (resp.status_code, resp.json()["error"]["code"]) == (403, "step_up_required")


async def test_a_personal_organisation_closes_only_with_the_account(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["dr_k"])).post(f"/v1/organisations/{world.solo}/close")
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "personal_organisation")


async def test_the_database_refuses_closing_for_anyone_but_an_owner(api_db: Database, world: World) -> None:
    async with api_db.request(scope_for(world["admin"])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("select app.close_organisation(%s)", (world.agency,))
