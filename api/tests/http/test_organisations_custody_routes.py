# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Custody of a served case's records (architecture 10.1 "Death or incapacity"; S15, S17; ADR 0012, legal check).

`POST /v1/support/custody-transfers`, behind the feature flag `support_custody_transfer` (404 while off):
current support staff only (anyone else 404), MFA in the last 10 minutes on every call. With evidence seen
(a short note about the expert, never the patient), support records that a served case's records pass,
read-only (migration 0032's `custodian` role), either
- to `custodian`: the person the named expert recorded as records custodian (`PUT /v1/me/custodian`), who
  must have an open account and be neither support staff nor the person recording it; or
- to `organisation`: the case's own team organisation (its current owners and admins hold the records).
A case never served is 409 `not_served`. While a transfer is in force a second one is 409
`custody_already_transferred` (one holder at a time), and a team organisation that has closed cannot hold them
(409 `organisation_closed`). Each transfer is in the case's audit trail; so is each refused attempt
(`support.custody_transfer_refused`, target `refused:<code>`), which stays although the request rolls back.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.auth.access import check_case_access
from app.core.db import Database
from app.core.errors import ApiError
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio

URL = "/v1/support/custody-transfers"
FLAG = "support_custody_transfer"
NOTE = "Death certificate seen; family letter on file"


@pytest.fixture
def staff(world: World) -> uuid.UUID:
    person = world.seed.person(full_name="Support")
    world.seed.support_staff(person)
    world.seed.flag(FLAG, "person", person)
    return person


def name_custodian(w: World, custodian: uuid.UUID, expert: str = "dr_k") -> None:
    email = w.admin.execute("select email from app.people where id = %s", (custodian,)).fetchone()
    assert email is not None
    w.admin.execute(
        "update app.people set records_custodian_name = 'Dr P', records_custodian_email = %s where id = %s",
        (email[0].upper(), w[expert]),
    )


def body(case: uuid.UUID, to: str = "custodian") -> dict[str, str]:
    return {"case_id": str(case), "to": to, "evidence_note": NOTE}


async def test_s15_a_served_case_passes_to_the_named_custodian_read_only(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection, api_db: Database
) -> None:
    case = world.solo_case(status="served")
    custodian = world.seed.person(full_name="Dr P")
    name_custodian(world, custodian)
    before = await (await client_for(custodian)).get(f"/v1/cases/{case}")
    assert before.status_code == 404

    resp = await (await client_for(staff)).post(URL, json=body(case))
    assert resp.status_code == 201, resp.text
    made = resp.json()
    assert (made["case_id"], made["to_person_id"], made["to_organisation_id"]) == (str(case), str(custodian), None)
    assert admin.execute(
        "select from_person_id, transferred_by, evidence_note from app.custody_transfers where id = %s", (made["id"],)
    ).fetchone() == (world["dr_k"], staff, NOTE)
    assert admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'support.custody_transferred'"
        " and person_id = %s",
        (case, staff),
    ).fetchone() == (1,)

    assert (await (await client_for(custodian)).get(f"/v1/cases/{case}")).status_code == 200
    async with api_db.request(scope_for(custodian)) as conn:
        access = await check_case_access(conn, case, "case.view_content")
        assert access.case_role == "custodian"
        assert set(access.allowed_actions) == {
            "case.view_metadata", "case.view_content", "case.view_audit", "case.view_certificate"
        }  # fmt: skip
        with pytest.raises(ApiError):
            await check_case_access(conn, case, "case.edit_setup")


async def test_a_served_team_case_passes_to_its_own_organisation(
    client_for: ClientFor, world: World, staff: uuid.UUID, api_db: Database
) -> None:
    case = world.team_case(status="served")
    resp = await (await client_for(staff)).post(URL, json=body(case, "organisation"))
    assert resp.status_code == 201, resp.text
    assert (resp.json()["to_person_id"], resp.json()["to_organisation_id"]) == (None, str(world.agency))
    async with api_db.request(scope_for(world["owner"])) as conn:
        assert (await check_case_access(conn, case, "case.view_content")).case_role == "custodian"


async def test_a_personal_practice_has_no_one_else_to_hold_it(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    resp = await (await client_for(staff)).post(URL, json=body(world.solo_case(status="served"), "organisation"))
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "personal_organisation")


async def test_an_unserved_case_is_refused(client_for: ClientFor, world: World, staff: uuid.UUID) -> None:
    case = world.solo_case()
    name_custodian(world, world.seed.person())
    resp = await (await client_for(staff)).post(URL, json=body(case))
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "not_served")


async def test_no_custodian_named_or_no_account(client_for: ClientFor, world: World, staff: uuid.UUID) -> None:
    case = world.solo_case(status="served")
    client = await client_for(staff)
    resp = await client.post(URL, json=body(case))
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "no_custodian")
    world.admin.execute(
        "update app.people set records_custodian_name = 'Dr Q', records_custodian_email = 'q@nowhere.test'"
        " where id = %s",
        (world["dr_k"],),
    )
    resp = await client.post(URL, json=body(case))
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "no_custodian")


async def test_a_closed_account_cannot_be_custodian(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    case = world.solo_case(status="served")
    custodian = world.seed.person()
    name_custodian(world, custodian)
    world.seed.close_account(custodian)
    resp = await (await client_for(staff)).post(URL, json=body(case))
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "custodian_account_closed")
    assert admin.execute("select count(*) from app.custody_transfers where case_id = %s", (case,)).fetchone() == (0,)


@pytest.mark.parametrize("who", ["recorder", "other_staff"])
async def test_support_staff_never_become_custodian(
    client_for: ClientFor, world: World, staff: uuid.UUID, who: str
) -> None:
    case = world.solo_case(status="served")
    target = staff
    if who == "other_staff":
        target = world.seed.person()
        world.seed.support_staff(target)
    name_custodian(world, target)
    resp = await (await client_for(staff)).post(URL, json=body(case))
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "custodian_not_allowed")


async def test_with_the_flag_off_the_route_is_not_there(client_for: ClientFor, world: World) -> None:
    person = world.seed.person()
    world.seed.support_staff(person)
    resp = await (await client_for(person)).post(URL, json=body(world.solo_case(status="served")))
    assert resp.status_code == 404


@pytest.mark.parametrize("second", ["custodian", "organisation"])
async def test_one_holder_at_a_time(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection, second: str
) -> None:
    case = world.team_case(status="served")
    name_custodian(world, world.seed.person())
    client = await client_for(staff)
    assert (await client.post(URL, json=body(case))).status_code == 201
    resp = await client.post(URL, json=body(case, second))
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "custody_already_transferred")
    assert admin.execute(
        "select count(*) from app.custody_transfers where case_id = %s and revoked_at is null", (case,)
    ).fetchone() == (1,)


@pytest.mark.parametrize("who", ["dr_k", "owner", "removed"])
async def test_anyone_but_current_staff_gets_404(client_for: ClientFor, world: World, who: str) -> None:
    person = world.seed.person() if who == "removed" else world[who]
    if who == "removed":
        world.seed.support_staff(person, removed=True)
    world.seed.flag(FLAG, "person", person)
    resp = await (await client_for(person)).post(URL, json=body(world.solo_case(status="served")))
    assert resp.status_code == 404


async def test_recording_needs_recent_mfa(client_for: ClientFor, world: World, staff: uuid.UUID) -> None:
    resp = await (await client_for(staff, mfa_age=STALE)).post(URL, json=body(world.solo_case(status="served")))
    assert (resp.status_code, resp.json()["error"]["code"]) == (403, "step_up_required")


@pytest.mark.parametrize(
    "bad",
    [{"evidence_note": ""}, {"evidence_note": "x" * 201}, {"to": "anyone"}, {"case_id": "nope"}, {"extra": 1}],
)
async def test_the_request_is_checked(
    client_for: ClientFor, world: World, staff: uuid.UUID, bad: dict[str, object]
) -> None:
    resp = await (await client_for(staff)).post(URL, json={**body(world.solo_case(status="served")), **bad})
    assert resp.status_code == 422


async def test_an_unknown_case_is_404(client_for: ClientFor, world: World, staff: uuid.UUID) -> None:
    resp = await (await client_for(staff)).post(URL, json=body(uuid.uuid4()))
    assert resp.status_code == 404


async def test_a_closed_organisation_cannot_hold_the_records(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    case = world.team_case(status="served")
    admin.execute("update app.organisations set closed_at = now() where id = %s", (world.agency,))
    resp = await (await client_for(staff)).post(URL, json=body(case, "organisation"))
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "organisation_closed")
    assert admin.execute("select count(*) from app.custody_transfers where case_id = %s", (case,)).fetchone() == (0,)


async def test_a_refused_attempt_stays_in_the_audit_trail(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    client = await client_for(staff)
    served = world.solo_case(status="served")
    unserved = world.solo_case()
    unknown = uuid.uuid4()
    attempts = [(served, "custodian", "no_custodian"), (served, "organisation", "personal_organisation"),
                (unserved, "custodian", "not_served"), (unknown, "custodian", "not_found")]  # fmt: skip
    for case, to, code in attempts:
        resp = await client.post(URL, json=body(case, to))
        assert resp.json()["error"]["code"] == code
    found = admin.execute(
        "select case_id, target from app.audit_log where action = 'support.custody_transfer_refused'"
        " and person_id = %s order by seq",
        (staff,),
    ).fetchall()
    assert found == [(case, f"refused:{code}") for case, _, code in attempts]
    assert admin.execute(
        "select count(*) from app.audit_log where action = 'support.custody_transferred' and person_id = %s",
        (staff,),
    ).fetchone() == (0,)


async def test_a_transfer_that_goes_through_records_no_refusal(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    case = world.team_case(status="served")
    resp = await (await client_for(staff)).post(URL, json=body(case, "organisation"))
    assert resp.status_code == 201, resp.text
    assert admin.execute(
        "select count(*) from app.audit_log where action = 'support.custody_transfer_refused' and case_id = %s",
        (case,),
    ).fetchone() == (0,)
