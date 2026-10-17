"""POST /v1/cases: a draft case for one of your organisations, with you as the named expert.

Architecture 5.2: jurisdiction, case type, specialty, label, party role, instructing party type, due
date and court track; the pack versions are stamped on the case. Permissions: owners, admins and
experts of the organisation may create one (stage 1: naming another expert waits for slice C/O).
Every value that names something from a pack must be a key of a combination this cell offers.
Runs under the fixture packs (rule 11).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests.support.db import ClientFor
from tests.support.packs import FIXTURE
from tests.support.seed import Seed

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.core.db import Database

pytestmark = pytest.mark.anyio


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    """An app serving the fixture packs' cell, so the fixture combination is offered."""
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    cell = str(FIXTURE.raw("jurisdiction", "pack.yaml")["cell"])
    return create_app(database=api_db, region=cell, auth=SupabaseAuth(auth_settings(auth_env)), mailer=OutboxMailer())


@pytest.fixture
def people(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "admin", "assistant", "viewer", "outsider")}
    p["org"] = seed.org(
        kind="team",
        members={p["expert"]: "expert", p["admin"]: "admin", p["assistant"]: "assistant", p["viewer"]: "viewer"},
    )
    p["other_org"] = seed.org(kind="personal", members={p["outsider"]: "owner"})
    return p


def body(org: uuid.UUID, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "organisation_id": str(org),
        "jurisdiction": "zz-test",
        "case_type": "zz-case",
        "specialty": "zz-specialty",
        "label": "Case 1",
        "party_role": "instructing_side",
        "instructing_party_type": "insurer",
        "due_date": "2027-03-01",
    }
    data.update(changes)
    return data


async def test_an_expert_creates_a_draft_case_named_to_themselves(
    client_for: ClientFor, people: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(people["expert"])
    resp = await client.post("/v1/cases", json=body(people["org"]))
    assert resp.status_code == 201, resp.text
    case = resp.json()
    assert case["status"] == "draft"
    assert case["named_expert_id"] == str(people["expert"])
    assert case["organisation_id"] == str(people["org"])
    assert case["expert_role"] == "assessor", "the case type's only expert role is the default"
    assert (case["party_role"], case["instructing_party_type"], case["due_date"]) == (
        "instructing_side",
        "insurer",
        "2027-03-01",
    )
    assert "fact.verify" in case["allowed_actions"]
    assert resp.headers["etag"] == '"1"'
    again = await client.get(f"/v1/cases/{case['id']}")
    assert again.status_code == 200

    row = admin.execute("select pack_versions from app.cases where id = %s", (case["id"],)).fetchone()
    assert row is not None
    assert row[0] == {
        "jurisdiction": f"zz-test@{FIXTURE.raw('jurisdiction', 'pack.yaml')['version']}",
        "case_type": f"zz-case@{FIXTURE.raw('case-type', 'pack.yaml')['version']}",
        "specialty": f"zz-specialty@{FIXTURE.raw('specialty', 'pack.yaml')['version']}",
    }
    audit = admin.execute(
        "select action, person_id from app.audit_log where case_id = %s order by seq desc limit 1", (case["id"],)
    ).fetchone()
    assert audit == ("case.created", people["expert"])


async def test_an_admin_may_create_a_case(client_for: ClientFor, people: dict[str, uuid.UUID]) -> None:
    resp = await (await client_for(people["admin"])).post("/v1/cases", json=body(people["org"]))
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("who", ["assistant", "viewer", "outsider"])
async def test_others_may_not_create_a_case_named_to_themselves(
    client_for: ClientFor, people: dict[str, uuid.UUID], admin: psycopg.Connection, who: str
) -> None:
    resp = await (await client_for(people[who])).post("/v1/cases", json=body(people["org"]))
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"
    count = admin.execute("select count(*) from app.cases where organisation_id = %s", (people["org"],)).fetchone()
    assert count == (0,)


@pytest.mark.parametrize(
    "changes",
    [
        {"jurisdiction": "nowhere"},
        {"specialty": "not-a-specialty"},
        {"party_role": "not-a-party"},
        {"instructing_party_type": "not-a-type"},
        {"expert_role": "not-a-role"},
        {"court_track": "not-a-track"},
        {"label": ""},
    ],
)
async def test_values_outside_the_offered_packs_are_refused(
    client_for: ClientFor, people: dict[str, uuid.UUID], changes: dict[str, str]
) -> None:
    resp = await (await client_for(people["expert"])).post("/v1/cases", json=body(people["org"], **changes))
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "invalid"


async def test_the_same_idempotency_key_creates_one_case(
    client_for: ClientFor, people: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(people["expert"])
    key = {"idempotency-key": str(uuid.uuid4())}
    first = await client.post("/v1/cases", json=body(people["org"]), headers=key)
    second = await client.post("/v1/cases", json=body(people["org"]), headers=key)
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["id"] == second.json()["id"]
    count = admin.execute("select count(*) from app.cases where organisation_id = %s", (people["org"],)).fetchone()
    assert count == (1,)


async def test_the_database_refuses_a_case_named_to_someone_else(api_dsn: str, people: dict[str, uuid.UUID]) -> None:
    """RLS repeats the check (rule 9): the restricted role cannot insert a case named to another person."""
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(people["expert"]),))
        conn.execute("select set_config('app.org_ids', %s, true)", (str(people["org"]),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into app.cases (organisation_id, named_expert_id, jurisdiction, case_type, specialty,"
                " expert_role, label) values (%s, %s, 'zz-test', 'zz-case', 'zz-specialty', 'assessor', 'x')",
                (people["org"], people["admin"]),
            )
