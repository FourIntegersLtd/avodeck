# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""A records custodian (architecture 5.4, 10.1 "Death or incapacity"; permissions.md "Person actions").

`PUT /v1/me/custodian` 🔒 names (or, with both empty, clears) the person who may be given read-only custody
of the expert's served cases if the expert can no longer act. Only the person themselves; it writes
`people.records_custodian_name` and `records_custodian_email`, and the audit row holds no name or email.
(legal check: custody itself is support's transfer, ADR 0012.)
"""

from __future__ import annotations

import psycopg
import pytest

from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio

URL = "/v1/me/custodian"


def stored(admin: psycopg.Connection, w: World) -> tuple[object, ...] | None:
    return admin.execute(
        "select records_custodian_name, records_custodian_email from app.people where id = %s", (w["dr_k"],)
    ).fetchone()


async def test_naming_a_custodian(client_for: ClientFor, world: World, admin: psycopg.Connection) -> None:
    resp = await (await client_for(world["dr_k"])).put(URL, json={"name": "Dr P Partner", "email": "P@Example.test"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"name": "Dr P Partner", "email": "p@example.test"}
    assert stored(admin, world) == ("Dr P Partner", "p@example.test")
    rows = admin.execute(
        "select target from app.audit_log where person_id = %s and action = 'person.custodian_changed'",
        (world["dr_k"],),
    ).fetchall()
    assert rows == [(f"person:{world['dr_k']}",)]


async def test_clearing_the_custodian(client_for: ClientFor, world: World, admin: psycopg.Connection) -> None:
    client = await client_for(world["dr_k"])
    await client.put(URL, json={"name": "Dr P", "email": "p@example.test"})
    resp = await client.put(URL, json={"name": None, "email": None})
    assert resp.status_code == 200, resp.text
    assert stored(admin, world) == (None, None)


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Dr P", "email": "not-an-email"},
        {"name": "", "email": "p@example.test"},
        {"name": "Dr P", "email": None},
        {"name": None, "email": "p@example.test"},
        {"name": "x" * 201, "email": "p@example.test"},
        {"name": "Dr P", "email": "p@example.test", "other": 1},
    ],
)
async def test_a_custodian_has_a_name_and_an_email(client_for: ClientFor, world: World, body: dict[str, object]) -> None:
    resp = await (await client_for(world["dr_k"])).put(URL, json=body)
    assert resp.status_code == 422


async def test_naming_a_custodian_needs_step_up_and_csrf(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    body = {"name": "Dr P", "email": "p@example.test"}
    stale = await (await client_for(world["dr_k"], mfa_age=STALE)).put(URL, json=body)
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "step_up_required")
    no_csrf = await (await client_for(world["dr_k"], csrf=False)).put(URL, json=body)
    assert no_csrf.status_code == 403
    assert stored(admin, world) == (None, None)
