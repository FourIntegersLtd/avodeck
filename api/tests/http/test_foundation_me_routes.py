"""GET /v1/me: the first call creates the person and their personal organisation (architecture 5.2, 10.1)."""

from __future__ import annotations

import uuid
from datetime import datetime

import psycopg
import pytest

from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


async def test_signed_out_gets_401(client_for: ClientFor) -> None:
    resp = await (await client_for(None)).get("/v1/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_signed_in"


async def test_first_call_creates_the_person_and_a_personal_organisation_they_own(
    client_for: ClientFor, admin: psycopg.Connection
) -> None:
    person = uuid.uuid4()
    client = await client_for(person, email=f"{person.hex[:10]}@Example.test", full_name="Dr Z")
    resp = await client.get("/v1/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["person"]["id"] == str(person)
    assert body["person"]["email"] == f"{person.hex[:10]}@example.test"
    assert body["person"]["home_region"] == "zz"
    assert body["expert"] is None
    assert body["mfa_enrolled"] is True  # nobody reaches /me without completing MFA
    [membership] = body["memberships"]
    assert (membership["kind"], membership["role"]) == ("personal", "owner")

    org = uuid.UUID(membership["organisation_id"])
    assert admin.execute("select kind, region from app.organisations where id = %s", (org,)).fetchone() == (
        "personal",
        "zz",
    )
    assert admin.execute(
        "select role from app.memberships where organisation_id = %s and person_id = %s and ended_at is null",
        (org, person),
    ).fetchall() == [("owner",)]
    assert admin.execute(
        "select count(*) from app.audit_log where person_id = %s and action = 'person.created'", (person,)
    ).fetchone() == (1,)


async def test_later_calls_create_nothing_new(client_for: ClientFor, admin: psycopg.Connection) -> None:
    person = uuid.uuid4()
    client = await client_for(person)
    first = (await client.get("/v1/me")).json()
    second = (await client.get("/v1/me")).json()
    assert first["memberships"] == second["memberships"]
    assert admin.execute("select count(*) from app.memberships where person_id = %s", (person,)).fetchone() == (1,)
    assert admin.execute("select count(*) from app.people where id = %s", (person,)).fetchone() == (1,)


async def test_timestamps_come_back_in_utc(client_for: ClientFor) -> None:
    body = (await (await client_for(uuid.uuid4())).get("/v1/me")).json()
    created = datetime.fromisoformat(body["person"]["created_at"])
    assert created.utcoffset() is not None and created.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
