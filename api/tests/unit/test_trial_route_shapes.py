"""Bad or unexpected requests to slice I's routes get a 4xx, never a 5xx (CI fuzzing, RFC 9110).

- A method a path does not offer is 405, even beside a templated route (`PATCH /concordance/{row_id}`
  must not claim the literal words `export` or `tell-instructing-party`), and before any sign-in check.
- The e-sign webhook refuses anything unsigned with 401, including when the cell has no secret yet: an
  unsigned call can never be a real provider event, so it is the caller's fault, not the server's.
"""

from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from app.main import create_app

pytestmark = pytest.mark.anyio

CASE = "00000000-0000-4000-8000-000000000000"
SECRET = "zz-route-shape-secret"


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(region="zz")), base_url="https://test")


@pytest.mark.parametrize(
    ("path", "methods"),
    [
        (f"/v1/cases/{CASE}/concordance/export?format=csv", {"GET"}),
        (f"/v1/cases/{CASE}/concordance/tell-instructing-party", {"POST"}),
    ],
)
async def test_a_method_the_path_does_not_offer_is_405(path: str, methods: set[str]) -> None:
    async with client() as c:
        res = await c.patch(path)
    assert res.status_code == 405
    assert {m.strip() for m in res.headers["allow"].split(",")} == methods


@pytest.mark.parametrize("body", [b"", b"not json", b'{"reference": 1}'])
async def test_an_unsigned_webhook_call_is_401_with_or_without_a_secret(
    body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ESIGN_WEBHOOK_SECRET", raising=False)
    async with client() as c:
        unset = await c.post("/v1/webhooks/esign", content=body)
        monkeypatch.setenv("ESIGN_WEBHOOK_SECRET", SECRET)
        unsigned = await c.post("/v1/webhooks/esign", content=body)
    assert unset.status_code == 401 and unset.json()["error"]["code"] == "bad_signature"
    assert unsigned.status_code == 401 and unsigned.json()["error"]["code"] == "bad_signature"


@pytest.mark.parametrize("body", [b"", b"not json", b'{"reference": 1}', b'{"reference": "x", "event": "other"}'])
async def test_a_signed_but_malformed_event_is_422(body: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESIGN_WEBHOOK_SECRET", SECRET)
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    async with client() as c:
        res = await c.post("/v1/webhooks/esign", content=body, headers={"x-avodeck-signature": signature})
    assert res.status_code == 422


async def test_the_webhook_secret_comes_from_the_cell_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read through `core.config.webhook_settings`, not the process environment directly (rule 5)."""
    from app.core.config import WebhookSettings
    from app.features.trial import routes_webhook

    monkeypatch.delenv("ESIGN_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(routes_webhook, "webhook_settings", lambda: WebhookSettings(esign_secret=SECRET))
    body = b"not json"
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    async with client() as c:
        res = await c.post("/v1/webhooks/esign", content=body, headers={"x-avodeck-signature": signature})
    assert res.status_code == 422
