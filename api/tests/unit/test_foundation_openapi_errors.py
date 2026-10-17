"""Every operation documents its error answers (architecture 5.1), so clients and the fuzzer know them.

Errors share one body, `{"error": {"code", "message", ...}}`, documented once as the `ApiError`
schema and given to every operation as its `default` response. A method a path does not offer
answers 405 with an `Allow` header (RFC 9110). Schemathesis checks both in CI.
"""

from __future__ import annotations

import httpx
import pytest

from app.main import create_app

pytestmark = pytest.mark.anyio

METHODS = {"get", "put", "post", "patch", "delete"}


def test_every_operation_documents_the_shared_error_body() -> None:
    doc = create_app().openapi()
    body = doc["components"]["schemas"]["ApiError"]
    assert body["required"] == ["error"]
    inner = doc["components"]["schemas"]["ApiErrorDetail"]
    assert set(inner["required"]) == {"code", "message"}
    missing = []
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method not in METHODS:
                continue
            default = op["responses"].get("default", {})
            ref = default.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
            if ref != "#/components/schemas/ApiError":
                missing.append(f"{method.upper()} {path}")
    assert not missing, f"no default error response: {missing}"


async def test_a_method_the_path_does_not_offer_answers_405_with_allow() -> None:
    app = create_app(region="zz")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        res = await client.request("TRACE", "/v1/packs")
    assert res.status_code == 405
    assert "GET" in res.headers.get("allow", "")
    assert res.json()["error"]["code"] == "method_not_allowed"
