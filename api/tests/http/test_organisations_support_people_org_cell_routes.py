# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Support console: a person, an organisation and the cell (support plan phase 2; permissions.md "Avodeck staff").

- `GET /v1/support/people?q=<email>`: the account's state, for a support call (MFA locked out, codes used
  up, an email change half done, why closing the account is refused). Never a name, the email itself, the
  custodian's details or a date of birth.
- `GET /v1/support/organisations/{id}`: members (role, dates), invites (state and dates, never the token or
  the email), how many owners and open cases, and when cases were reassigned. Never the organisation's name.
- `GET /v1/support/cell/status`: speech and the key service configured, the worker's heartbeat age, how many
  jobs wait and how many are paused.

Current staff only (anyone else 404); a person or organisation look is in the audit trail.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from tests.fixtures.test_organisations_world import World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio

PERSON_FIELDS = {"person_id", "created_at", "closed_at", "mfa_enrolled", "mfa_locked_until", "recovery_codes_left",
                 "last_mfa_at", "pending_email_change", "custodian_set", "blocking_case_ids_for_closure"}  # fmt: skip


@pytest.fixture
def staff(world: World) -> uuid.UUID:
    person = world.seed.person(full_name="Support")
    world.seed.support_staff(person)
    return person


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def test_a_person_shows_their_mfa_codes_email_change_custodian_and_closure_blockers(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    k = world["dr_k"]
    world.seed.session(k, mfa_age=timedelta(minutes=30))
    for used in (False, False, True):
        admin.execute(
            "insert into app.mfa_recovery_codes (person_id, code_hash, used_at) values (%s, %s, %s)",
            (k, uuid.uuid4().hex, datetime.now().astimezone() if used else None),
        )
    admin.execute(
        "insert into app.mfa_attempts (person_id, failures, window_started_at)"
        " values (%s, 5, now() - interval '5 minutes')",
        (k,),
    )
    admin.execute(
        "insert into app.email_changes (person_id, new_email, old_token_hash, new_token_hash)"
        " values (%s, 'new-address@example.test', %s, %s)",
        (k, uuid.uuid4().hex, uuid.uuid4().hex),
    )
    world.seed.records_custodian(k, world["dr_m"])
    blocking = world.solo_case()
    exported = world.solo_case()
    job = world.seed.job(exported, kind="export_case", requested_by=k)
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    world.team_case()  # a team case never blocks closing the account

    resp = await (await client_for(staff)).get("/v1/support/people", params={"q": world.email("dr_k").upper()})
    assert resp.status_code == 200, resp.text
    [person] = resp.json()["people"]
    assert set(person) == PERSON_FIELDS
    assert person["person_id"] == str(k)
    assert person["closed_at"] is None
    assert person["mfa_enrolled"] is True
    assert person["recovery_codes_left"] == 2
    assert person["pending_email_change"] is True
    assert person["custodian_set"] is True
    assert person["blocking_case_ids_for_closure"] == [str(blocking)]
    locked = _when(person["mfa_locked_until"]) - datetime.now().astimezone()
    assert timedelta(minutes=9) < locked <= timedelta(minutes=10)
    ago = datetime.now().astimezone() - _when(person["last_mfa_at"])
    assert timedelta(minutes=29) < ago < timedelta(minutes=31)
    text = resp.text
    for secret in (world.email("dr_k"), world.email("dr_m"), "new-address", "Dr K", "Named Custodian"):
        assert secret.lower() not in text.lower()


async def test_a_person_with_nothing_set_answers_plain_defaults(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    resp = await (await client_for(staff)).get("/v1/support/people", params={"q": world.email("outsider")})
    [person] = resp.json()["people"]
    assert {k: v for k, v in person.items() if k not in ("person_id", "created_at")} == {
        "closed_at": None,
        "mfa_enrolled": None,  # never signed in: not known
        "mfa_locked_until": None,
        "recovery_codes_left": 0,
        "last_mfa_at": None,
        "pending_email_change": False,
        "custodian_set": False,
        "blocking_case_ids_for_closure": [],
    }


async def test_an_old_lockout_a_finished_email_change_and_a_closed_account(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    v = world["viewer"]
    admin.execute(
        "insert into app.mfa_attempts (person_id, failures, window_started_at)"
        " values (%s, 7, now() - interval '20 minutes')",
        (v,),
    )
    admin.execute(
        "insert into app.email_changes (person_id, new_email, old_token_hash, new_token_hash, completed_at)"
        " values (%s, 'done@example.test', %s, %s, now())",
        (v, uuid.uuid4().hex, uuid.uuid4().hex),
    )
    world.seed.close_account(v)
    [person] = (await (await client_for(staff)).get("/v1/support/people", params={"q": world.email("viewer")})).json()[
        "people"
    ]
    assert person["mfa_locked_until"] is None
    assert person["pending_email_change"] is False
    assert person["closed_at"] is not None


async def test_a_person_look_is_audited_and_an_unknown_email_finds_nobody(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    client = await client_for(staff)
    assert (await client.get("/v1/support/people", params={"q": "nobody@example.test"})).json() == {"people": []}
    assert (await client.get("/v1/support/people", params={"q": "not-an-email"})).json() == {"people": []}
    await client.get("/v1/support/people", params={"q": world.email("dr_m")})
    assert admin.execute(
        "select target from app.audit_log where action = 'support.person_looked_up' and person_id = %s", (staff,)
    ).fetchall() == [(f"person:{world['dr_m']}",)]


async def test_an_organisation_shows_members_invites_owners_open_cases_and_reassignments(
    client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    org = world.agency
    world.seed.end_membership(org, world["viewer"])
    invites = {}
    for status, expires in (("pending", "7 days"), ("pending", "-1 day"), ("cancelled", "7 days")):
        invites[(status, expires)] = admin.execute(
            "insert into app.invites (organisation_id, email, role, invited_by, token_hash, status, expires_at)"
            " values (%s, 'invitee@example.test', 'expert', %s, %s, %s, now() + %s::interval) returning id",
            (org, world["owner"], uuid.uuid4().hex + uuid.uuid4().hex, status, expires),
        ).fetchone()[0]  # type: ignore[index]
    moved = world.team_case()
    world.team_case(status="withdrawn")
    world.team_case(status="deleted")
    world.solo_case()  # another organisation's case
    admin.execute(
        "insert into app.audit_log (case_id, person_id, action, target)"
        " values (%s, %s, 'case.named_expert_changed', %s)",
        (moved, world["owner"], f"case:{moved}"),
    )

    resp = await (await client_for(staff)).get(f"/v1/support/organisations/{org}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"organisation_id", "kind", "created_at", "closed_at", "owner_count", "open_case_count",
                         "members", "invites", "reassignments"}  # fmt: skip
    assert (body["organisation_id"], body["kind"], body["closed_at"]) == (str(org), "team", None)
    assert body["owner_count"] == 1
    assert body["open_case_count"] == 1
    roles = sorted((m["role"], m["ended_at"] is not None) for m in body["members"])
    assert roles == [("admin", False), ("assistant", False), ("expert", False), ("expert", False), ("owner", False),
                     ("viewer", True)]  # fmt: skip
    assert set(body["members"][0]) == {"person_id", "role", "started_at", "ended_at"}
    states = {i["id"]: i["state"] for i in body["invites"]}
    assert states == {
        str(invites[("pending", "7 days")]): "pending",
        str(invites[("pending", "-1 day")]): "expired",
        str(invites[("cancelled", "7 days")]): "cancelled",
    }
    assert set(body["invites"][0]) == {"id", "role", "state", "sent_at", "expires_at"}
    assert [(r["case_id"]) for r in body["reassignments"]] == [str(moved)]
    for secret in ("Agency", "invitee", "token", "@example.test"):
        assert secret not in resp.text
    assert admin.execute(
        "select organisation_id, target from app.audit_log where action = 'support.org_looked_up' and person_id = %s",
        (staff,),
    ).fetchall() == [(org, f"organisation:{org}")]


async def test_an_unknown_organisation_is_404(client_for: ClientFor, staff: uuid.UUID) -> None:
    assert (await (await client_for(staff)).get(f"/v1/support/organisations/{uuid.uuid4()}")).status_code == 404


async def test_the_cell_status_counts_waiting_and_paused_work_and_the_worker_heartbeat(
    app: FastAPI, client_for: ClientFor, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    client = await client_for(staff)
    before = (await client.get("/v1/support/cell/status")).json()
    assert set(before) == {"speech_ok", "key_service_ok", "worker_heartbeat_age_seconds", "queue_depth", "paused_jobs"}
    assert (before["speech_ok"], before["key_service_ok"]) == (False, False)  # the test cell sets neither
    case = world.team_case()
    for _ in range(2):
        world.seed.job(case)
    paused = world.seed.job(case)
    admin.execute("update app.jobs set status = 'running' where id = %s", (paused,))
    admin.execute("update app.jobs set status = 'paused', error_code = 'cost_guard' where id = %s", (paused,))
    admin.execute("delete from procrastinate.procrastinate_workers")
    admin.execute(
        "insert into procrastinate.procrastinate_workers (last_heartbeat) values (now() - interval '30 seconds')"
    )
    app.state.speech = object()
    app.state.crypto = object()

    after = (await client.get("/v1/support/cell/status")).json()
    assert after["queue_depth"] - before["queue_depth"] == 2
    assert after["paused_jobs"] - before["paused_jobs"] == 1
    assert 25 <= after["worker_heartbeat_age_seconds"] <= 90
    assert (after["speech_ok"], after["key_service_ok"]) == (True, True)


async def test_no_worker_heartbeat_answers_none(
    client_for: ClientFor, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    admin.execute("delete from procrastinate.procrastinate_workers")
    assert (await (await client_for(staff)).get("/v1/support/cell/status")).json()[
        "worker_heartbeat_age_seconds"
    ] is None


@pytest.mark.parametrize("who", ["dr_k", "owner", "outsider", "former"])
async def test_anyone_but_current_staff_gets_404(client_for: ClientFor, world: World, who: str) -> None:
    person = world.seed.person() if who == "former" else world[who]
    if who == "former":
        world.seed.support_staff(person, removed=True)
    client = await client_for(person)
    assert (await client.get("/v1/support/people", params={"q": world.email("dr_k")})).status_code == 404
    assert (await client.get(f"/v1/support/organisations/{world.agency}")).status_code == 404
    assert (await client.get("/v1/support/cell/status")).status_code == 404


async def test_the_database_functions_answer_current_staff_only(
    api_db: Database, world: World, staff: uuid.UUID
) -> None:
    calls = (
        ("select app.support_person(%s, 5, interval '15 minutes')", (world.email("dr_k"),)),
        ("select app.support_organisation(%s)", (world.agency,)),
        ("select app.support_cell_status()", ()),
    )
    for person, answered in ((staff, True), (world["dr_k"], False), (world["owner"], False)):
        for sql, args in calls:
            async with api_db.request(scope_for(person)) as conn:
                row = await (await conn.execute(sql, args)).fetchone()
            assert (row is not None and row[0] is not None) == answered, (person, sql)
