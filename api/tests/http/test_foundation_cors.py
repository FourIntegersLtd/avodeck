"""CORS for the web app's own origin, and nothing else (ADR 0031).

The browser talks straight to the API's origin, so every credentialed request is cross-origin. The
API answers CORS for the origins in `APP_ORIGIN` only, never `*` (which a browser refuses with
credentials anyway), and a preflight is answered before any session, CSRF or database work: it
carries no cookies, so it cannot be authenticated.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.core.cors import ALLOW_HEADERS, EXPOSE_HEADERS, MAX_AGE
from app.main import create_app

pytestmark = pytest.mark.anyio

APP = "https://avodeck.com"
OTHER = "https://avodeck.com.attacker.test"


def a_client() -> httpx.AsyncClient:
    auth = SimpleNamespace(
        settings=SimpleNamespace(app_origin=APP, app_origins=(APP,), cookie_domain="avodeck.com")
    )
    app = create_app(database=SimpleNamespace(), region="zz", auth=auth)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://api.avodeck.com")


async def test_a_preflight_for_a_chunk_put_is_answered_without_a_session() -> None:
    async with a_client() as client:
        resp = await client.request(
            "OPTIONS",
            "/v1/uploads/11111111-1111-1111-1111-111111111111/chunks/1",
            headers={
                "origin": APP,
                "access-control-request-method": "PUT",
                "access-control-request-headers": "content-type, x-chunk-sha256, x-csrf-token",
            },
        )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == APP
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert "PUT" in resp.headers["access-control-allow-methods"]
    assert resp.headers["access-control-max-age"] == MAX_AGE
    assert "origin" in resp.headers["vary"].lower()
    allowed = {h.strip() for h in resp.headers["access-control-allow-headers"].split(",")}
    # Every header the client sends, or the browser drops the request before the API sees it.
    assert {"content-type", "x-csrf-token", "x-chunk-sha256", "idempotency-key", "if-match"} <= allowed
    assert "*" not in allowed


async def test_a_preflight_from_another_origin_gets_no_cors_headers() -> None:
    async with a_client() as client:
        resp = await client.request(
            "OPTIONS", "/v1/cell", headers={"origin": OTHER, "access-control-request-method": "POST"}
        )
    assert resp.status_code == 403
    assert "access-control-allow-origin" not in resp.headers


async def test_a_credentialed_response_names_the_origin_and_exposes_the_headers_the_client_reads() -> None:
    async with a_client() as client:
        resp = await client.get("/v1/cell", headers={"origin": APP})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == APP
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert "origin" in resp.headers["vary"].lower()
    exposed = {h.strip() for h in resp.headers["access-control-expose-headers"].split(",")}
    assert {"etag", "idempotent-replayed"} <= exposed
    assert "*" not in exposed


async def test_another_origin_is_refused_and_told_nothing() -> None:
    async with a_client() as client:
        resp = await client.get("/v1/cell", headers={"origin": OTHER})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "bad_origin"
    assert "access-control-allow-origin" not in resp.headers
    # Still varies on Origin, so a shared cache cannot serve one origin's answer to another.
    assert "origin" in resp.headers["vary"].lower()


async def test_a_change_without_an_origin_is_refused() -> None:
    """A browser always sends Origin on a change. Without one we cannot tell where it came from, and
    the CSRF cookie alone is no longer same-origin protection."""
    async with a_client() as client:
        resp = await client.post("/v1/auth/sign-in", json={"email": "a@b.test", "password": "x" * 12})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "bad_origin"


async def test_a_read_without_an_origin_still_works() -> None:
    """Health checks and server-to-server reads send no Origin."""
    async with a_client() as client:
        resp = await client.get("/v1/cell")
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_the_header_lists_are_not_wildcards() -> None:
    """A browser ignores `*` on a credentialed request, so a wildcard here would fail silently."""
    assert "*" not in ALLOW_HEADERS and "*" not in EXPOSE_HEADERS


def test_only_a_machine_route_may_skip_the_origin_requirement() -> None:
    """A browser route refused without an `Origin`; a signed webhook has its signature instead."""
    from app.core.http import PublicRoute, public_router

    assert PublicRoute.require_origin is True
    machine = public_router(machine=True).route_class
    assert issubclass(machine, PublicRoute) and machine.require_origin is False
