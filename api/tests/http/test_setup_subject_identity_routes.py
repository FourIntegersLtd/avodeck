"""PUT /cases/{id}/subject/identity: the subject's name, other names and date of birth, sealed (rule 7 needs
them stored; rules 6 and 9 keep them out of logs and away from everyone but the named expert).

The fixture packs read dates month first, so 03/04/1960 is 4 March, which shows the pack's order is used.
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.packs import default_library
from app.features.setup.identity import SUBJECT_DOB, SUBJECT_NAME, SUBJECT_OTHER_NAMES
from tests.support.db import ClientFor, scope_for
from tests.support.packs import FIXTURE
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

NAME = "Zelda Quuxmore"
OTHER = ["Z Quux", "Zelda Maiden"]
BODY = {"name": NAME, "other_names": OTHER, "date_of_birth": "03/04/1960"}
CELL = str(FIXTURE.raw("jurisdiction", "pack.yaml")["cell"])


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], crypto: CaseCrypto) -> FastAPI:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(
        database=api_db,
        region=CELL,
        auth=SupabaseAuth(auth_settings(auth_env)),
        mailer=OutboxMailer(),
        packs=default_library(),
        crypto=crypto,
    )


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant, viewer, outsider = (seed.person() for _ in range(4))
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant", viewer: "expert"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    return {"expert": expert, "assistant": assistant, "viewer": viewer, "outsider": outsider, "case": case}


def url(world: dict[str, uuid.UUID], tail: str) -> str:
    return f"/v1/cases/{world['case']}/{tail}"


async def test_the_named_expert_records_the_identity_sealed(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection, crypto: CaseCrypto, api_db: Database
) -> None:
    client = await client_for(world["expert"])
    assert (await client.get(url(world, "subject"))).json()["identity_recorded"] is False
    done = await client.put(url(world, "subject/identity"), json=BODY)
    assert done.status_code == 204, done.text
    row = admin.execute(
        "select name, other_names, date_of_birth from app.case_subject_identity where case_id = %s", (world["case"],)
    ).fetchone()
    assert row is not None
    name, other, dob = (bytes(v) for v in row)
    assert NAME.encode() not in name and b"1960" not in dob and b"Quux" not in other
    async with api_db.request(scope_for(world["expert"])) as conn:
        opened = [
            (await crypto.decrypt(conn, world["case"], v, purpose=p)).decode()
            for v, p in ((name, SUBJECT_NAME), (other, SUBJECT_OTHER_NAMES), (dob, SUBJECT_DOB))
        ]
    assert opened[0] == NAME
    assert json.loads(opened[1]) == OTHER
    assert opened[2] == "1960-03-04"  # the pack reads month first


async def test_a_second_save_replaces_and_the_date_is_optional(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(world["expert"])
    await client.put(url(world, "subject/identity"), json=BODY)
    again = await client.put(url(world, "subject/identity"), json={"name": "Someone Else", "date_of_birth": None})
    assert again.status_code == 204, again.text
    rows = admin.execute(
        "select other_names, date_of_birth from app.case_subject_identity where case_id = %s", (world["case"],)
    ).fetchall()
    assert len(rows) == 1 and rows[0][1] is None


@pytest.mark.parametrize("bad", ["not a date", "31/31/1960", "1960"])
async def test_a_date_that_is_not_a_whole_date_is_refused(
    client_for: ClientFor, world: dict[str, uuid.UUID], bad: str
) -> None:
    client = await client_for(world["expert"])
    assert (await client.put(url(world, "subject/identity"), json={**BODY, "date_of_birth": bad})).status_code == 422


async def test_a_blank_name_is_refused(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    client = await client_for(world["expert"])
    assert (await client.put(url(world, "subject/identity"), json={**BODY, "name": "  "})).status_code == 422


async def test_only_the_named_expert_records_it(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    for who, status in (("assistant", 403), ("viewer", 403), ("outsider", 404)):
        client = await client_for(world[who])
        assert (await client.put(url(world, "subject/identity"), json=BODY)).status_code == status, who


async def test_the_subject_says_only_whether_it_is_recorded_never_the_values(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    await (await client_for(world["expert"])).put(url(world, "subject/identity"), json=BODY)
    for who in ("expert", "assistant", "viewer"):
        got = await (await client_for(world[who])).get(url(world, "subject"))
        assert got.json()["identity_recorded"] is True
        assert NAME not in got.text and "1960" not in got.text and "Quux" not in got.text


async def test_the_save_is_audited_without_the_values(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    await (await client_for(world["expert"])).put(url(world, "subject/identity"), json=BODY)
    rows = admin.execute(
        "select action, target from app.audit_log where case_id = %s and action = 'subject.identity_set'",
        (world["case"],),
    ).fetchall()
    assert rows == [("subject.identity_set", f"case:{world['case']}")]
