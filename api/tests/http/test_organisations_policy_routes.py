# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Organisation policy (architecture 10.1 "What can sit on either"; core/contracts/org_policy.py).

`PUT /v1/organisations/{org_id}/policy` 🔒 (owner or admin, `org.set_policy`) sets the report template the
organisation requires and its retention limit in whole years. The body is checked against the policy's
shape: `required_template_id` must be one of the organisation's own templates; `max_retention_years` 1-100;
no other keys. Either may be null, and then the person's own defaults apply (`org_policy()` answers None).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.contracts.org_policy import OrgPolicy, org_policy
from app.core.db import Database
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio


def template(admin: psycopg.Connection, owner_type: str, owner: uuid.UUID) -> uuid.UUID:
    tid = uuid.uuid4()
    admin.execute(
        "insert into app.report_templates (id, owner_type, owner_id, filename, storage_path, sha256, size_bytes,"
        " version) values (%s, %s, %s, 'report.docx', %s, %s, 10, 1)",
        (tid, owner_type, owner, f"templates/{tid}", "a" * 64),
    )
    return tid


def url(w: World, org: uuid.UUID | None = None) -> str:
    return f"/v1/organisations/{org or w.agency}/policy"


@pytest.mark.parametrize("who", ["owner", "admin"])
async def test_owner_or_admin_requires_a_template_and_caps_retention(
    client_for: ClientFor, world: World, admin: psycopg.Connection, api_db: Database, who: str
) -> None:
    tid = template(admin, "organisation", world.agency)
    resp = await (await client_for(world[who])).put(
        url(world), json={"required_template_id": str(tid), "max_retention_years": 8}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["policy"] == {"required_template_id": str(tid), "max_retention_years": 8}
    async with api_db.request(scope_for(world["dr_k"])) as conn:
        assert await org_policy(conn, world.agency) == OrgPolicy(tid, 8)
    assert admin.execute(
        "select count(*) from app.audit_log where organisation_id = %s and action = 'organisation.policy_changed'",
        (world.agency,),
    ).fetchone() == (1,)


async def test_clearing_the_policy_gives_back_personal_defaults(
    client_for: ClientFor, world: World, admin: psycopg.Connection, api_db: Database
) -> None:
    client = await client_for(world["owner"])
    assert (await client.put(url(world), json={"max_retention_years": 3})).status_code == 200
    resp = await client.put(url(world), json={"required_template_id": None, "max_retention_years": None})
    assert resp.status_code == 200, resp.text
    async with api_db.request(scope_for(world["dr_k"])) as conn:
        assert await org_policy(conn, world.agency) == OrgPolicy(None, None)


@pytest.mark.parametrize("owner_type", ["person", "other_org"])
async def test_the_template_must_be_the_organisations_own(
    client_for: ClientFor, world: World, admin: psycopg.Connection, owner_type: str
) -> None:
    if owner_type == "person":
        tid = template(admin, "person", world["owner"])
    else:
        tid = template(admin, "organisation", world.solo)
    resp = await (await client_for(world["owner"])).put(url(world), json={"required_template_id": str(tid)})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid"
    unknown = await (await client_for(world["owner"])).put(url(world), json={"required_template_id": str(uuid.uuid4())})
    assert unknown.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"max_retention_years": 0},
        {"max_retention_years": 101},
        {"max_retention_years": 2.5},
        {"max_retention_years": "7"},
        {"required_template_id": "not-an-id"},
        {"something_else": 1},
    ],
)
async def test_the_policy_shape_is_checked(client_for: ClientFor, world: World, body: dict[str, object]) -> None:
    resp = await (await client_for(world["owner"])).put(url(world), json=body)
    assert resp.status_code == 422


@pytest.mark.parametrize("who", ["dr_k", "assistant", "viewer"])
async def test_other_members_cannot_set_policy(client_for: ClientFor, world: World, who: str) -> None:
    resp = await (await client_for(world[who])).put(url(world), json={"max_retention_years": 5})
    assert resp.status_code == 403


async def test_setting_policy_needs_step_up_and_membership(client_for: ClientFor, world: World) -> None:
    stale = await (await client_for(world["owner"], mfa_age=STALE)).put(url(world), json={"max_retention_years": 5})
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "step_up_required")
    outsider = await (await client_for(world["outsider"])).put(url(world), json={"max_retention_years": 5})
    assert outsider.status_code == 404
