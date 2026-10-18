"""POST /v1/cases naming another expert, and the subject and period fields (F2, slice C).

Permissions (organisation actions): owners, admins and experts create a case named to themselves;
an assistant creates one naming an owner, admin or expert member of the organisation and joins the
case as its assistant. `subject_status` is a key from the case's packs; the period runs from
`period_start` to `period_end` (start on or before end). Runs under the fixture packs (rule 11).
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
    }
    data.update(changes)
    return data


async def test_an_assistant_creates_a_case_naming_an_expert_and_joins_it(
    client_for: ClientFor, people: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    resp = await (await client_for(people["assistant"])).post(
        "/v1/cases", json=body(people["org"], named_expert_id=str(people["expert"]))
    )
    assert resp.status_code == 201, resp.text
    case = resp.json()
    assert case["named_expert_id"] == str(people["expert"])
    assert "case.edit_setup" in case["allowed_actions"]
    assert "question.confirm" not in case["allowed_actions"]
    member = admin.execute(
        "select case_role, added_by from app.case_members where case_id = %s and person_id = %s",
        (case["id"], people["assistant"]),
    ).fetchone()
    assert member == ("assistant", people["assistant"])
    seen = await (await client_for(people["expert"])).get(f"/v1/cases/{case['id']}")
    assert seen.status_code == 200
    assert "question.confirm" in seen.json()["allowed_actions"]


@pytest.mark.parametrize("named", ["viewer", "outsider", "nobody"])
async def test_an_assistant_must_name_an_expert_member(
    client_for: ClientFor, people: dict[str, uuid.UUID], admin: psycopg.Connection, named: str
) -> None:
    named_id = str(people.get(named, uuid.uuid4()))
    resp = await (await client_for(people["assistant"])).post(
        "/v1/cases", json=body(people["org"], named_expert_id=named_id)
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "invalid"
    count = admin.execute("select count(*) from app.cases where organisation_id = %s", (people["org"],)).fetchone()
    assert count == (0,)


@pytest.mark.parametrize("who", ["expert", "admin"])
async def test_others_name_only_themselves(client_for: ClientFor, people: dict[str, uuid.UUID], who: str) -> None:
    client = await client_for(people[who])
    other = people["admin" if who == "expert" else "expert"]
    resp = await client.post("/v1/cases", json=body(people["org"], named_expert_id=str(other)))
    assert resp.status_code == 403, resp.text
    resp = await client.post("/v1/cases", json=body(people["org"], named_expert_id=str(people[who])))
    assert resp.status_code == 201, resp.text
    assert resp.json()["named_expert_id"] == str(people[who])


async def test_the_subject_and_the_period_are_kept_and_editable(
    client_for: ClientFor, people: dict[str, uuid.UUID]
) -> None:
    client = await client_for(people["expert"])
    resp = await client.post(
        "/v1/cases",
        json=body(people["org"], subject_status="present", period_start="2024-02-01", period_end="2024-08-31"),
    )
    assert resp.status_code == 201, resp.text
    case = resp.json()
    assert (case["subject_status"], case["period_start"], case["period_end"]) == ("present", "2024-02-01", "2024-08-31")
    resp = await client.patch(
        f"/v1/cases/{case['id']}",
        json={"subject_status": "dependent", "period_end": "2024-09-30"},
        headers={"if-match": resp.headers["etag"]},
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["subject_status"], resp.json()["period_end"]) == ("dependent", "2024-09-30")


@pytest.mark.parametrize(
    "changes",
    [
        {"subject_status": "living"},  # a launch-pack key: not offered by the fixture packs
        {"subject_status": "not-a-status"},
        {"period_start": "2024-09-01", "period_end": "2024-08-01"},
    ],
)
async def test_subject_and_period_values_are_checked(
    client_for: ClientFor, people: dict[str, uuid.UUID], changes: dict[str, str]
) -> None:
    client = await client_for(people["expert"])
    resp = await client.post("/v1/cases", json=body(people["org"], **changes))
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "invalid"
    created = await client.post("/v1/cases", json=body(people["org"]))
    resp = await client.patch(
        f"/v1/cases/{created.json()['id']}", json=changes, headers={"if-match": created.headers["etag"]}
    )
    assert resp.status_code == 422, resp.text


def test_the_database_lets_an_assistant_name_an_expert_member_only(api_dsn: str, people: dict[str, uuid.UUID]) -> None:
    """RLS repeats the check (rule 9)."""
    insert = (
        "insert into app.cases (organisation_id, named_expert_id, jurisdiction, case_type, specialty,"
        " expert_role, label) values (%s, %s, 'zz-test', 'zz-case', 'zz-specialty', 'assessor', 'x')"
    )
    for named, allowed in ((people["expert"], True), (people["viewer"], False), (people["assistant"], False)):
        with psycopg.connect(api_dsn) as conn, conn.transaction():
            conn.execute("select set_config('app.person_id', %s, true)", (str(people["assistant"]),))
            if allowed:
                assert conn.execute(insert, (people["org"], named)).rowcount == 1
            else:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(insert, (people["org"], named))
