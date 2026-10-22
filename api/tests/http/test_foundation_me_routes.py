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


async def test_the_short_name_is_the_pack_title_for_the_profession_and_the_surname(
    client_for: ClientFor, admin: psycopg.Connection
) -> None:
    person = uuid.uuid4()
    client = await client_for(person, full_name="Alex Quill Mortimer")
    first = (await client.get("/v1/me")).json()
    assert first["person"]["greeting_name"] is None  # no expert profile yet, so no title to use
    admin.execute(
        "insert into app.experts (person_id, profession, default_jurisdiction) values (%s, 'therapist', 'zz-test')",
        (person,),
    )
    assert (await client.get("/v1/me")).json()["person"]["greeting_name"] == "Zed Mortimer"


async def test_no_short_name_when_the_pack_has_no_title_for_the_profession(
    client_for: ClientFor, admin: psycopg.Connection
) -> None:
    person = uuid.uuid4()
    client = await client_for(person, full_name="Alex Mortimer")
    await client.get("/v1/me")
    admin.execute(
        "insert into app.experts (person_id, profession, default_jurisdiction) values (%s, 'surveyor', 'zz-test')",
        (person,),
    )
    assert (await client.get("/v1/me")).json()["person"]["greeting_name"] is None


async def test_password_changed_at_is_the_last_password_reset_and_empty_before_one(
    client_for: ClientFor, admin: psycopg.Connection
) -> None:
    person = uuid.uuid4()
    client = await client_for(person)
    assert (await client.get("/v1/me")).json()["person"]["password_changed_at"] is None
    for age in ("90 days", "3 days"):
        admin.execute(
            "insert into app.audit_log (person_id, action, target, at)"
            " values (%s, 'auth.password_reset', %s, now() - %s::interval)",
            (person, f"person:{person}", age),
        )
    changed = datetime.fromisoformat((await client.get("/v1/me")).json()["person"]["password_changed_at"])
    assert 2 <= (datetime.now(changed.tzinfo) - changed).days <= 3


async def test_each_membership_says_how_many_people_are_in_the_organisation(
    client_for: ClientFor, admin: psycopg.Connection
) -> None:
    person, colleague, ended = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    client = await client_for(person)
    [mine] = (await client.get("/v1/me")).json()["memberships"]
    assert mine["member_count"] == 1
    await (await client_for(colleague)).get("/v1/me")
    await (await client_for(ended)).get("/v1/me")
    team = admin.execute(
        "insert into app.organisations (name, kind, region) values ('Team', 'team', 'zz') returning id"
    ).fetchone()
    assert team is not None
    for who, role, gone in ((person, "owner", False), (colleague, "admin", False), (ended, "admin", True)):
        admin.execute(
            "insert into app.memberships (organisation_id, person_id, role, ended_at)"
            " values (%s, %s, %s, case when %s then now() end)",
            (team[0], who, role, gone),
        )
    counts = {m["kind"]: m["member_count"] for m in (await client.get("/v1/me")).json()["memberships"]}
    assert counts == {"personal": 1, "team": 2}
