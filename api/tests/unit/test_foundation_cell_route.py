"""GET /v1/cell: what the sign-in screen shows about this cell, before anyone is signed in.

The place files are kept comes from the cell's environment; unset or blank is null (the screen then
leaves that line out).
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.core.config import cell_public
from app.main import create_app

pytestmark = pytest.mark.anyio


def a_client() -> httpx.AsyncClient:
    auth = SimpleNamespace(settings=SimpleNamespace(app_origin="https://zz.invalid"))
    app = create_app(database=SimpleNamespace(), region="zz", auth=auth)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def test_cell_public_reads_the_environment_and_blanks_become_null() -> None:
    assert cell_public({"STORAGE_PLACE": " zz-place "}).storage_place == "zz-place"
    assert cell_public({"STORAGE_PLACE": ""}).storage_place is None


async def test_the_cell_is_public_and_returns_the_cells_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PLACE", "zz place")
    async with a_client() as client:
        resp = await client.get("/v1/cell")  # no session cookie
    assert resp.status_code == 200
    assert resp.json() == {"storage_place": "zz place"}


async def test_missing_values_come_back_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORAGE_PLACE", raising=False)
    async with a_client() as client:
        resp = await client.get("/v1/cell")
    assert resp.status_code == 200
    assert resp.json() == {"storage_place": None}
