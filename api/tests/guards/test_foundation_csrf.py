"""CSRF (architecture 9, Login): every state-changing request on a signed-in route carries the CSRF token.

The session cookie is sent by the browser automatically, so a state-changing request also needs the
CSRF token (double submit: the `av_csrf` cookie copied into the `X-CSRF-Token` header, checked against
the session). The route table is scanned, so a new route cannot forget it. Only the routes used before
anyone is signed in are exempt, and they are named here; adding one needs the foundation owner.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.main import create_app
from tests.support.db import ClientFor

UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Used before a session exists (no CSRF token to send). SameSite=Strict and the Origin check cover them.
SIGNED_OUT_ROUTES = {
    ("POST", "/v1/auth/sign-in"),
    ("POST", "/v1/auth/mfa/recover"),
    ("POST", "/v1/auth/password/forgot"),
    ("POST", "/v1/auth/password/reset"),
    ("POST", "/v1/me/email-change/confirm"),
    # The colleague's spot-check answers: no session at all; the link token and the emailed code are the gate.
    ("POST", "/v1/spot-checks/{token}/responses"),
    # The e-sign provider's webhook: no session; an HMAC of the body with the cell's secret is the gate.
    ("POST", "/v1/webhooks/esign"),
}


def unsafe_routes() -> list[tuple[str, str]]:
    return sorted(
        (method, r.path)
        for r in create_app().routes
        if isinstance(r, APIRoute)
        for method in (r.methods or set()) & UNSAFE
    )


def signed_in_unsafe_routes() -> list[tuple[str, str]]:
    return [r for r in unsafe_routes() if r not in SIGNED_OUT_ROUTES]


def _fill(path: str) -> str:
    return re.sub(r"\{\w+\}", lambda _: str(uuid.uuid4()), path)


def _kwargs(method: str) -> dict[str, Any]:
    return {} if method == "DELETE" else {"json": {}}


def test_the_signed_out_routes_exist() -> None:
    assert SIGNED_OUT_ROUTES <= set(unsafe_routes())


def test_there_are_signed_in_state_changing_routes_to_check() -> None:
    assert len(signed_in_unsafe_routes()) >= 5


def test_only_the_named_routes_work_without_a_session() -> None:
    """Every other state-changing route is built with the signed-in router (session, MFA, CSRF)."""
    from app.core.http import SessionRoute

    app = create_app()
    open_routes = {
        (method, r.path)
        for r in app.routes
        if isinstance(r, APIRoute) and not isinstance(r, SessionRoute)
        for method in (r.methods or set()) & UNSAFE
    }
    assert open_routes == SIGNED_OUT_ROUTES


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), signed_in_unsafe_routes())
async def test_state_changing_route_refuses_a_request_without_the_csrf_token(
    client_for: ClientFor, method: str, path: str
) -> None:
    client = await client_for(uuid.uuid4(), csrf=False, **{"if-match": '"1"'})
    resp = await client.request(method, _fill(path), **_kwargs(method))
    assert resp.status_code == 403, (method, path, resp.status_code)
    assert resp.json()["error"]["code"] == "csrf_required"


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), signed_in_unsafe_routes())
async def test_state_changing_route_refuses_a_wrong_csrf_token(client_for: ClientFor, method: str, path: str) -> None:
    client = await client_for(uuid.uuid4(), csrf=False, **{"x-csrf-token": "not-the-token", "if-match": '"1"'})
    resp = await client.request(method, _fill(path), **_kwargs(method))
    assert resp.status_code == 403, (method, path, resp.status_code)
    assert resp.json()["error"]["code"] == "csrf_required"


@pytest.mark.anyio
async def test_reading_needs_no_csrf_token(client_for: ClientFor) -> None:
    resp = await (await client_for(uuid.uuid4(), csrf=False)).get("/v1/me")
    assert resp.status_code == 200, resp.text


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), sorted(SIGNED_OUT_ROUTES)[:1] + signed_in_unsafe_routes()[:1])
async def test_a_request_from_another_origin_is_refused(client_for: ClientFor, method: str, path: str) -> None:
    client = await client_for(uuid.uuid4(), origin="https://elsewhere.example")
    resp = await client.request(method, _fill(path), **_kwargs(method))
    assert resp.status_code == 403, (method, path, resp.status_code)
    assert resp.json()["error"]["code"] == "bad_origin"
