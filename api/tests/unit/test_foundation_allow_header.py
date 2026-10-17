"""A 405 lists exactly the methods the path offers, even when they are on separate routes (RFC 9110).

`/v1/cases/{case_id}` has GET and PATCH on separate routes, so `Allow` names both.
`/v1/me/sessions/revoke-all` offers only POST: `DELETE /v1/me/sessions/{session_id}` takes a UUID,
so it must not claim the word `revoke-all`.
"""

from __future__ import annotations

import httpx
import pytest

from app.main import create_app

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("path", "methods"),
    [("/v1/cases/00000000-0000-4000-8000-000000000000", {"GET", "PATCH"}), ("/v1/me/sessions/revoke-all", {"POST"})],
)
async def test_allow_names_exactly_the_methods_the_path_offers(path: str, methods: set[str]) -> None:
    app = create_app(region="zz")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        res = await client.request("OPTIONS", path)
    assert res.status_code == 405
    allowed = {m.strip() for m in res.headers["allow"].split(",")}
    assert allowed == methods
