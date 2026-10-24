# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Avodeck's support console (architecture 10.1 "Support access"; scenario S17; F38; permissions.md "Avodeck staff").

- `GET /v1/support/cases?q=`: find a case by its id or by its named expert's email. Metadata only: ids,
  status, dates, organisation kind, whether it was served and whether a grant is active. Never the case's
  label, names or anything from its pages.
- `GET /v1/support/cases/{case_id}`: the same for one case, with its grants and whether support may open
  it now (an active grant). Every look is written to the case's audit trail.
- Only current support staff (`require_support_lookup`), in a session whose MFA was completed at sign-in
  (not the 10-minute window: ADR 0020); anyone else gets 404, as if the console did not exist.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import psycopg
import pytest

from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio

METADATA = {"case_id", "status", "organisation_id", "organisation_kind", "created_at", "served", "grant_active",
            "grant_expires_at"}  # fmt: skip


@pytest.fixture
def staff(world: World) -> uuid.UUID:
    person = world.seed.person(full_name="Support")
    world.seed.support_staff(person)
    return person


async def test_s17_support_finds_a_case_by_id_or_expert_email_and_sees_metadata_only(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    case = world.team_case(status="served")
    other = world.solo_case()
    client = await client_for(staff)
    by_id = await client.get("/v1/support/cases", params={"q": str(case)})
    assert by_id.status_code == 200, by_id.text
    [found] = by_id.json()["cases"]
    assert set(found) == METADATA
    assert (found["case_id"], found["status"], found["organisation_kind"], found["served"], found["grant_active"]) == (
        str(case), "served", "team", True, False,
    )  # fmt: skip
    by_email = await client.get("/v1/support/cases", params={"q": world.email("dr_k").upper()})
    assert {c["case_id"] for c in by_email.json()["cases"]} == {str(case), str(other)}
    assert "Case Z" not in by_id.text and "Dr K" not in by_email.text
    nothing = await client.get("/v1/support/cases", params={"q": "nobody@example.test"})
    assert nothing.json() == {"cases": []}
    # Every case a search returns is a look at that case: it goes to the case's audit trail.
    looks = world.admin.execute(
        "select case_id, count(*) from app.audit_log where action = 'support.case_looked_up' and person_id = %s"
        " group by case_id",
        (staff,),
    ).fetchall()
    assert dict(looks) == {case: 2, other: 1}


async def test_one_case_with_its_grants_and_every_look_audited(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    case = world.team_case()
    client = await client_for(staff)
    resp = await client.get(f"/v1/support/cases/{case}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["case"]["case_id"] == str(case) and body["content_access"] is False and body["grants"] == []
    gid = world.seed.support_grant(case, world["dr_k"], expires_in=timedelta(hours=2))
    body = (await client.get(f"/v1/support/cases/{case}")).json()
    assert body["content_access"] is True and body["case"]["grant_active"] is True
    assert [g["id"] for g in body["grants"]] == [str(gid)]
    assert set(body["grants"][0]) == {"id", "granted_to", "reason", "granted_at", "expires_at", "revoked_at", "active"}
    assert admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'support.case_looked_up'"
        " and person_id = %s",
        (case, staff),
    ).fetchone() == (2,)
    assert (await client.get(f"/v1/support/cases/{uuid.uuid4()}")).status_code == 404


async def test_a_grant_for_someone_else_gives_this_staff_member_no_content(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    other = world.seed.person()
    world.seed.support_staff(other)
    case = world.team_case()
    world.seed.support_grant(case, world["dr_k"], granted_to=other)
    body = (await (await client_for(staff)).get(f"/v1/support/cases/{case}")).json()
    assert body["content_access"] is False


@pytest.mark.parametrize("who", ["dr_k", "owner", "removed"])
async def test_anyone_but_current_staff_gets_404(client_for: ClientFor, world: World, who: str) -> None:
    person = world[who] if who != "removed" else world.seed.person()
    if who == "removed":
        world.seed.support_staff(person, removed=True)
    client = await client_for(person)
    case = world.team_case()
    assert (await client.get("/v1/support/cases", params={"q": str(case)})).status_code == 404
    assert (await client.get(f"/v1/support/cases/{case}")).status_code == 404


async def test_console_lookups_need_session_mfa_not_the_ten_minute_window(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    client = await client_for(staff, mfa_age=STALE)
    case = world.team_case()
    assert (await client.get("/v1/support/cases", params={"q": str(case)})).status_code == 200
    assert (await client.get(f"/v1/support/cases/{case}")).status_code == 200


@pytest.mark.parametrize("q", ["", "x" * 321])
async def test_the_search_needs_a_sensible_query(client_for: ClientFor, world: World, staff: uuid.UUID, q: str) -> None:
    resp = await (await client_for(staff)).get("/v1/support/cases", params={"q": q})
    assert resp.status_code == 422
