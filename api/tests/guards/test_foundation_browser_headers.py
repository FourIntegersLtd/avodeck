"""The browser-facing response headers the API must send itself (ADR 0031).

Until the API had its own origin, every `/v1/*` answer reached the browser through `avodeck.com`,
where Vercel added `X-Content-Type-Options`, `X-Frame-Options`, `frame-ancestors 'none'` and
`Referrer-Policy` to everything (`web/vercel.json`). Nothing adds them on the way from
`api.avodeck.com`, so the API sends them. Without them a sibling host can frame a report PDF: the
request is same-site, so the session cookie goes with it.

Also here: only a route that proves itself some other way may skip the "a change names its origin"
rule, and today that is the signed e-sign webhook alone.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.core.http import PublicRoute
from app.main import create_app

pytestmark = pytest.mark.anyio

APP = "https://avodeck.com"
REQUIRED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
    "referrer-policy": "no-referrer",
}


def _app() -> object:
    auth = SimpleNamespace(settings=SimpleNamespace(app_origin=APP, app_origins=(APP,), cookie_domain=None))
    app = create_app(database=SimpleNamespace(), region="zz", auth=auth)  # type: ignore[arg-type]

    @app.get("/v1/_boom")
    async def boom() -> None:  # pragma: no cover - raised, never returns
        raise RuntimeError("something the code did not expect")

    @app.get("/v1/_teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=418)

    return app


def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="https://api.avodeck.com")


@pytest.mark.parametrize("path", ["/v1/cell", "/v1/_teapot", "/v1/_boom"])
async def test_every_answer_tells_the_browser_not_to_sniff_or_frame_it(path: str) -> None:
    app = _app()
    async with _client(app) as client:
        resp = await client.get(path)
    for name, value in REQUIRED.items():
        assert resp.headers.get(name) == value, f"{path} is missing {name}"


async def test_a_failure_the_code_did_not_expect_still_answers_the_page() -> None:
    """A 500 without CORS headers reaches the page as a network error, so the expert is told to check
    their connection instead of being told to try again."""
    app = _app()
    async with _client(app) as client:
        resp = await client.get("/v1/_boom", headers={"origin": APP})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "server_error"
    assert resp.headers["access-control-allow-origin"] == APP
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_only_the_signed_webhook_may_skip_naming_its_origin() -> None:
    app = create_app(
        database=SimpleNamespace(),  # type: ignore[arg-type]
        region="zz",
        auth=SimpleNamespace(settings=SimpleNamespace(app_origin=APP, app_origins=(APP,), cookie_domain=None)),  # type: ignore[arg-type]
    )
    skipping = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, PublicRoute) and type(route).require_origin is False
        for method in sorted(route.methods or ())
    }
    assert skipping == {("POST", "/v1/webhooks/esign")}, (
        "a browser route must not use public_router(machine=True): a change with no Origin would be "
        "accepted from anywhere"
    )
