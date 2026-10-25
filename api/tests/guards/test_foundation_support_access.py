"""Rule 9, scenario S17 and failure mode F38: Avodeck support looks at a case only under a live grant.

The case role `support` applies only while the person is in `app.support_staff` (not removed) and
the case has a `support_access_grants` row from its named expert that is not revoked and has not
expired (checked on every read, so a grant ends by itself after 24 hours). Under it support takes
only the three read actions, every request is written to the case's audit trail as
`support_access.viewed`, and every call needs MFA in the last 10 minutes (`aal2`). The database
answers the same through `app.can_access_case()`, so row-level security repeats it.

The support console (ADR 0020, ADR 0023): every route needs current staff and a session whose MFA was
completed at sign-in, within the 30-minute idle and 12-hour limits. None needs a fresh MFA code (the
10-minute window): read-only lookups (`GET /v1/support/...`) use `require_support_lookup`, and routes
that change something use `require_support_staff`, which also keeps CSRF and, where the route asks for
it, the Idempotency-Key (founder decision 2026-10-25, ADR 0023).

Support actions (`SUPPORT_ACTIONS`, under `/v1/support/`): each is behind its own flag, answers
current staff only (anyone else 404, as if the route did not exist), does not ask for a fresh code,
and writes `support.<action>` to the case's audit trail (job actions) or the person's (MFA), with ids
and a reason code only (rule 6). Every route under `/v1/support/` depends on `require_support_staff`,
or `require_support_lookup` for a read-only `GET`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.core.auth.access import check_case_access, require_case_access
from app.core.auth.permissions import CASE_ACTIONS
from app.core.auth.session import Session
from app.core.auth.support import require_support_lookup, require_support_staff
from app.core.db import Database
from app.core.errors import ApiError
from app.main import create_app
from tests.support.db import ClientFor, scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

READS = ("case.view_metadata", "case.view_content", "case.view_audit")
WRITES = sorted(set(CASE_ACTIONS) - set(READS))


@dataclass
class Desk:
    expert: uuid.UUID
    staff: uuid.UUID
    case: uuid.UUID
    other_case: uuid.UUID


@pytest.fixture
def desk(seed: Seed) -> Desk:
    expert, staff = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    seed.support_staff(staff)
    return Desk(expert, staff, seed.case(org, expert), seed.case(org, expert))


async def answer(api_db: Database, person: uuid.UUID, case: uuid.UUID, action: str) -> tuple[int, bool]:
    """(HTTP-like status from require_case_access, what the database says) for one action."""
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute("select app.can_access_case(%s, %s)", (case, action))
        row = await cur.fetchone()
        assert row is not None
        try:
            await check_case_access(conn, case, action)
        except ApiError as err:
            return err.status, row[0]
    return 200, row[0]


async def assert_sees_nothing(api_db: Database, person: uuid.UUID, case: uuid.UUID) -> None:
    for action in sorted(CASE_ACTIONS):
        assert await answer(api_db, person, case, action) == (404, False), action


async def test_staff_without_a_grant_see_nothing(api_db: Database, desk: Desk) -> None:
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_a_live_grant_opens_the_case_for_reading_only(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    for action in READS:
        assert await answer(api_db, desk.staff, desk.case, action) == (200, True), action
    for action in WRITES:
        assert await answer(api_db, desk.staff, desk.case, action) == (403, False), action


async def test_a_grant_for_any_staff_member_opens_the_case(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=None)
    assert await answer(api_db, desk.staff, desk.case, "case.view_content") == (200, True)


async def test_a_grant_opens_only_its_own_case(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    await assert_sees_nothing(api_db, desk.staff, desk.other_case)


async def test_an_expired_grant_opens_nothing(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff, expires_in=timedelta(minutes=-1))
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_expiry_is_checked_on_every_read(
    api_db: Database, seed: Seed, admin: psycopg.Connection, desk: Desk
) -> None:
    grant = seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    assert await answer(api_db, desk.staff, desk.case, "case.view_content") == (200, True)
    admin.execute(
        "update app.support_access_grants set expires_at = now() - interval '1 second' where id = %s", (grant,)
    )
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_a_revoked_grant_opens_nothing(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff, revoked=True)
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_someone_no_longer_staff_sees_nothing(api_db: Database, seed: Seed, desk: Desk) -> None:
    former = seed.person()
    seed.support_staff(former, removed=True)
    seed.support_grant(desk.case, desk.expert, granted_to=None)
    await assert_sees_nothing(api_db, former, desk.case)


async def test_a_grant_does_not_make_someone_staff(api_db: Database, seed: Seed, desk: Desk) -> None:
    stranger = seed.person()
    seed.support_grant(desk.case, desk.expert, granted_to=stranger)
    await assert_sees_nothing(api_db, stranger, desk.case)


async def test_a_grant_for_another_staff_member_does_not_open_it(api_db: Database, seed: Seed, desk: Desk) -> None:
    colleague = seed.person()
    seed.support_staff(colleague)
    seed.support_grant(desk.case, desk.expert, granted_to=colleague)
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_only_the_named_experts_grant_counts(api_db: Database, seed: Seed, desk: Desk) -> None:
    """A grant row someone else wrote (or one from before the case was reassigned) opens nothing."""
    seed.support_grant(desk.case, seed.person(), granted_to=desk.staff)
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_a_grant_on_a_deleted_case_opens_nothing(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    seed.case_status(desk.case, "deleted")
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_every_view_under_the_support_role_is_in_the_case_audit_trail(
    api_db: Database, seed: Seed, admin: psycopg.Connection, desk: Desk
) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    for action in ("case.view_content", "case.view_content", "case.view_audit"):
        assert (await answer(api_db, desk.staff, desk.case, action))[0] == 200
    await answer(api_db, desk.expert, desk.case, "case.view_content")  # the expert's own views are not support views
    await answer(api_db, desk.staff, desk.case, "fact.verify")  # refused: nothing seen
    rows = admin.execute(
        "select person_id, target from app.audit_log where case_id = %s and action = 'support_access.viewed'"
        " order by seq",
        (desk.case,),
    ).fetchall()
    assert rows == [(desk.staff, f"case:{desk.case}")] * 3


# --- Case routes under a grant keep the 10-minute window; the console does not (ADR 0023) ---------------


def _request(person: uuid.UUID, *, mfa_age: timedelta | None, aal: str = "aal2") -> Any:
    now = datetime.now(UTC)
    session = Session(
        id=uuid.uuid4(),
        person_id=person,
        email="staff@example.test",
        full_name=None,
        csrf_hash="0" * 64,
        aal=aal,
        mfa_enrolled=True,
        mfa_verified_at=None if mfa_age is None else now - mfa_age,
        last_seen_at=now,
        auth_access_token=None,
        auth_refresh_token=None,
        auth_expires_at=None,
    )
    return SimpleNamespace(state=SimpleNamespace(session=session))


async def test_case_routes_under_the_support_role_need_a_fresh_mfa(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    dependency = require_case_access("case.view_content")
    async with api_db.request(scope_for(desk.staff)) as conn:
        access = await dependency(_request(desk.staff, mfa_age=timedelta(minutes=1)), desk.case, conn)
        assert access.case_role == "support"
        with pytest.raises(ApiError) as err:
            await dependency(_request(desk.staff, mfa_age=timedelta(minutes=11)), desk.case, conn)
    assert (err.value.status, err.value.code) == (403, "step_up_required")
    async with api_db.request(scope_for(desk.expert)) as conn:  # the case's own people are not asked
        await dependency(_request(desk.expert, mfa_age=timedelta(hours=3)), desk.case, conn)


async def test_the_support_console_needs_current_staff_but_not_a_fresh_mfa(api_db: Database, seed: Seed) -> None:
    staff, former, stranger = seed.person(), seed.person(), seed.person()
    seed.support_staff(staff)
    seed.support_staff(former, removed=True)
    assert require_support_staff.step_up is False  # the route scanners see it
    async with api_db.request(scope_for(staff)) as conn:
        for age in (timedelta(minutes=2), timedelta(minutes=11), timedelta(hours=11)):
            assert (await require_support_staff(_request(staff, mfa_age=age), conn)).person_id == staff
        for request in (_request(staff, mfa_age=None), _request(staff, mfa_age=timedelta(0), aal="aal1")):
            with pytest.raises(ApiError) as no_mfa:
                await require_support_staff(request, conn)
            assert (no_mfa.value.status, no_mfa.value.code) == (403, "mfa_required")
    for person in (former, stranger):
        async with api_db.request(scope_for(person)) as conn:
            with pytest.raises(ApiError) as missing:
                await require_support_staff(_request(person, mfa_age=timedelta(0)), conn)
        assert missing.value.status == 404


# --- Every support route: current staff and session MFA, never the 10-minute window (ADR 0023) ------------


def _calls(dependant: Dependant) -> list[Any]:
    return [c for d in dependant.dependencies for c in (d.call, *_calls(d))]


def _support_routes() -> list[tuple[str, str, list[Any]]]:
    return sorted(
        ((method, r.path, _calls(r.dependant)) for r in create_app().routes
         if isinstance(r, APIRoute) and r.path.startswith("/v1/support")
         for method in r.methods or ()),
        key=lambda t: (t[1], t[0]),
    )  # fmt: skip


def test_every_support_route_needs_current_staff_and_none_needs_a_fresh_mfa() -> None:
    routes = _support_routes()
    lookups = [(m, p) for m, p, _ in routes if m == "GET"]
    changes = [(m, p) for m, p, _ in routes if m != "GET"]
    assert ("GET", "/v1/support/cases") in lookups and ("POST", "/v1/support/custody-transfers") in changes
    assert ("POST", "/v1/support/accounts") in changes
    for method, path, calls in routes:
        assert (require_support_lookup if method == "GET" else require_support_staff) in calls, (method, path)
        assert not any(getattr(c, "step_up", False) is True for c in calls), (method, path)


async def test_the_lookup_gate_needs_current_staff_and_mfa_done_this_session(api_db: Database, seed: Seed) -> None:
    staff, former, stranger = seed.person(), seed.person(), seed.person()
    seed.support_staff(staff)
    seed.support_staff(former, removed=True)
    assert require_support_lookup.step_up is False
    async with api_db.request(scope_for(staff)) as conn:
        for age in (timedelta(minutes=2), timedelta(minutes=30), timedelta(hours=11)):
            assert (await require_support_lookup(_request(staff, mfa_age=age), conn)).person_id == staff
        for request in (_request(staff, mfa_age=None), _request(staff, mfa_age=timedelta(0), aal="aal1")):
            with pytest.raises(ApiError) as no_mfa:
                await require_support_lookup(request, conn)
            assert (no_mfa.value.status, no_mfa.value.code) == (403, "mfa_required")
    for person in (former, stranger):
        async with api_db.request(scope_for(person)) as conn:
            with pytest.raises(ApiError) as missing:
                await require_support_lookup(_request(person, mfa_age=timedelta(minutes=30)), conn)
        assert missing.value.status == 404


async def test_mfa_at_sign_in_30_minutes_ago_can_search_and_record_a_custody_transfer(
    client_for: ClientFor, seed: Seed, desk: Desk
) -> None:
    seed.flag("support_custody_transfer", "person", desk.staff)
    client = await client_for(desk.staff, mfa_age=timedelta(minutes=30))
    found = await client.get("/v1/support/cases", params={"q": str(desk.case)})
    assert found.status_code == 200, found.text
    assert [c["case_id"] for c in found.json()["cases"]] == [str(desk.case)]
    assert (await client.get(f"/v1/support/cases/{desk.case}")).status_code == 200
    resp = await client.post(
        "/v1/support/custody-transfers",
        json={"case_id": str(desk.case), "to": "organisation", "evidence_note": "Letter on file"},
    )
    assert resp.status_code == 201, resp.text


async def test_support_lookups_refuse_the_signed_out_and_non_staff(
    client_for: ClientFor, seed: Seed, desk: Desk
) -> None:
    signed_out = await client_for(None)
    assert (await signed_out.get("/v1/support/cases", params={"q": "x"})).status_code == 401
    for person in (desk.expert, seed.person()):
        client = await client_for(person, mfa_age=timedelta(minutes=30))
        assert (await client.get("/v1/support/cases", params={"q": str(desk.case)})).status_code == 404
        assert (await client.get(f"/v1/support/cases/{desk.case}")).status_code == 404


# --- Support actions: current staff, its own flag, and an audit row; no fresh MFA (ADR 0023) ---------------


@dataclass(frozen=True)
class Target:
    path: str
    case_id: uuid.UUID | None  # the trail the row goes on: a case's, or None for a person's
    target: str


@dataclass(frozen=True)
class SupportAction:
    flag: str
    audit_action: str
    body: dict[str, str]
    make: Callable[[Seed, psycopg.Connection, Desk], Target]


def _job_in(seed: Seed, admin: psycopg.Connection, case: uuid.UUID, status: str, error_code: str) -> uuid.UUID:
    job = seed.job(case)
    admin.execute("update app.jobs set status = 'running', attempts = 3 where id = %s", (job,))
    admin.execute("update app.jobs set status = %s, error_code = %s where id = %s", (status, error_code, job))
    return job


def _paused_job(seed: Seed, admin: psycopg.Connection, desk: Desk) -> Target:
    job = _job_in(seed, admin, desk.case, "paused", "cost_guard")
    return Target(f"/v1/support/jobs/{job}/resume", desk.case, f"job:{job}/reason:service_restored")


def _failed_job(seed: Seed, admin: psycopg.Connection, desk: Desk) -> Target:
    job = _job_in(seed, admin, desk.case, "failed", "job_error")
    return Target(f"/v1/support/jobs/{job}/requeue", desk.case, f"job:{job}/reason:fault_fixed")


SUPPORT_ACTIONS = {
    "job_resume": SupportAction("support_job_resume", "support.job_resumed", {"reason_code": "service_restored"},
                                _paused_job),
    "job_requeue": SupportAction("support_job_requeue", "support.job_requeued", {"reason_code": "fault_fixed"},
                                 _failed_job),
}  # fmt: skip
TARGET_SHAPE = re.compile(r"^[a-z_]+:[0-9a-f-]{36}/reason:[a-z_]+$")  # ids and a code: nothing else (rule 6)


def _audit_rows(admin: psycopg.Connection, action: str, person: uuid.UUID) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select case_id, target from app.audit_log where action = %s and person_id = %s order by seq", (action, person)
    ).fetchall()


@pytest.mark.parametrize("name", sorted(SUPPORT_ACTIONS))
async def test_a_support_action_is_not_there_for_anyone_but_current_staff(
    name: str, client_for: ClientFor, admin: psycopg.Connection, seed: Seed, desk: Desk
) -> None:
    action = SUPPORT_ACTIONS[name]
    former, stranger = seed.person(), seed.person()
    seed.support_staff(former, removed=True)
    target = action.make(seed, admin, desk)
    for person in (former, stranger, desk.expert):
        seed.flag(action.flag, "person", person)
        response = await (await client_for(person)).post(target.path, json=action.body)
        assert response.status_code == 404, (name, response.text)
        assert _audit_rows(admin, action.audit_action, person) == []


@pytest.mark.parametrize("name", sorted(SUPPORT_ACTIONS))
async def test_a_support_action_does_not_need_a_fresh_mfa(
    name: str, client_for: ClientFor, admin: psycopg.Connection, seed: Seed, desk: Desk
) -> None:
    action = SUPPORT_ACTIONS[name]
    seed.flag(action.flag, "person", desk.staff)
    target = action.make(seed, admin, desk)
    response = await (await client_for(desk.staff, mfa_age=timedelta(hours=3))).post(target.path, json=action.body)
    assert response.status_code == 200, response.text
    assert _audit_rows(admin, action.audit_action, desk.staff) == [(target.case_id, target.target)]


@pytest.mark.parametrize("name", sorted(SUPPORT_ACTIONS))
async def test_a_support_action_is_off_until_its_flag_is_on(
    name: str, client_for: ClientFor, admin: psycopg.Connection, seed: Seed, desk: Desk
) -> None:
    action = SUPPORT_ACTIONS[name]
    target = action.make(seed, admin, desk)
    response = await (await client_for(desk.staff)).post(target.path, json=action.body)
    assert response.status_code == 404
    assert _audit_rows(admin, action.audit_action, desk.staff) == []


@pytest.mark.parametrize("name", sorted(SUPPORT_ACTIONS))
async def test_a_support_action_writes_its_audit_row_with_ids_and_a_reason_code_only(
    name: str, client_for: ClientFor, admin: psycopg.Connection, seed: Seed, desk: Desk
) -> None:
    action = SUPPORT_ACTIONS[name]
    seed.flag(action.flag, "person", desk.staff)
    target = action.make(seed, admin, desk)
    response = await (await client_for(desk.staff)).post(target.path, json=action.body)
    assert response.status_code == 200, response.text
    assert _audit_rows(admin, action.audit_action, desk.staff) == [(target.case_id, target.target)]
    assert TARGET_SHAPE.match(target.target)
    assert action.audit_action.startswith("support.")


async def test_every_support_route_needs_current_staff(app: Any) -> None:
    """A new route under /v1/support/ cannot forget the gate: its staff gate (lookup gate for a GET)."""

    def calls(dependant: Any) -> set[Any]:
        found = {dependant.call}
        for sub in dependant.dependencies:
            found |= calls(sub)
        return found

    support = [r for r in app.routes if getattr(r, "path", "").startswith("/v1/support/")]
    assert {f"{m} {r.path}" for r in support for m in r.methods} >= {
        "POST /v1/support/jobs/{job_id}/resume",
        "POST /v1/support/jobs/{job_id}/requeue",
    }
    for route in support:
        gate = require_support_lookup if route.methods == {"GET"} else require_support_staff
        assert gate in calls(route.dependant), route.path
