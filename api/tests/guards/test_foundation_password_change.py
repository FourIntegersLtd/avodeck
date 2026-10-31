"""A person with a temporary password chooses their own before anything else (ADR 0024; threat model T27).

`app.people.must_change_password` is set when Avodeck support makes the account or a new temporary
password. While it is set, the server refuses every signed-in route with 403 `password_change_required`
except the few the person needs to get there: where the session stands, who they are, the MFA steps of
sign-in, signing out, and `POST /v1/auth/password/change` itself. The block lives in the core
`SessionRoute`, so a new route cannot skip it: this scan names every route allowed through.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.routing import APIRoute

from app.core.http import SessionRoute
from app.main import create_app
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

# The only signed-in routes a person who must change their password may use.
ALLOWED = {
    "GET /v1/auth/session",
    "GET /v1/me",
    "POST /v1/auth/mfa/enroll",
    "POST /v1/auth/mfa/verify",
    "POST /v1/auth/sign-out",
    "POST /v1/auth/password/change",
}


def _exempt(route: SessionRoute) -> bool:
    return bool(route.mfa_pending or route.password_pending)


def test_only_the_named_routes_let_a_person_with_a_temporary_password_through() -> None:
    signed_in = [r for r in create_app().routes if isinstance(r, APIRoute) and isinstance(r, SessionRoute)]
    assert len(signed_in) > 50  # the scan sees the whole app
    through = {f"{m} {r.path}" for r in signed_in if _exempt(r) for m in r.methods or ()}
    assert through == ALLOWED


def _must_change(admin: psycopg.Connection, person: uuid.UUID) -> None:
    admin.execute("update app.people set must_change_password = true where id = %s", (person,))


async def test_every_other_signed_in_route_answers_password_change_required(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    person = seed.person()
    seed.org(kind="personal", members={person: "owner"})
    _must_change(admin, person)
    client = await client_for(person)
    for method, path, body in (
        ("GET", "/v1/today", None),
        ("GET", "/v1/notifications", None),
        ("GET", "/v1/auth/mfa/recovery-codes", None),
        ("POST", "/v1/auth/mfa/recovery-codes", None),
        ("GET", "/v1/support/cases?q=x", None),
        ("POST", "/v1/cases", {"title": "x"}),
    ):
        resp = await client.request(method, path, json=body)
        assert (resp.status_code, resp.json()["error"]["code"]) == (403, "password_change_required"), path


async def test_where_the_session_stands_and_who_they_are_say_so(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    person = seed.person()
    _must_change(admin, person)
    client = await client_for(person)
    status = await client.get("/v1/auth/session")
    me = await client.get("/v1/me")
    assert status.status_code == me.status_code == 200, (status.text, me.text)
    assert status.json()["must_change_password"] is True
    assert me.json()["must_change_password"] is True


async def test_nobody_else_is_blocked(client_for: ClientFor, seed: Seed) -> None:
    person = seed.person()
    seed.org(kind="personal", members={person: "owner"})
    client = await client_for(person)
    assert (await client.get("/v1/today")).status_code == 200
    body: dict[str, Any] = (await client.get("/v1/auth/session")).json()
    assert body["must_change_password"] is False
