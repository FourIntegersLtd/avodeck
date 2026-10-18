"""GET /v1/cell: what the sign-in screen shows about this cell, before anyone is signed in.

Support phone and hours and the place files are kept come from the cell's environment; a value the
cell does not set is null (the screen then leaves that line out).
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.core.config import cell_public
from app.main import create_app

pytestmark = pytest.mark.anyio

NAMES = ("SUPPORT_PHONE", "SUPPORT_HOURS", "STORAGE_PLACE")


def a_client() -> httpx.AsyncClient:
    auth = SimpleNamespace(settings=SimpleNamespace(app_origin="https://zz.invalid"))
    app = create_app(database=SimpleNamespace(), region="zz", auth=auth)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def test_cell_public_reads_the_environment_and_blanks_become_null() -> None:
    got = cell_public({"SUPPORT_PHONE": " 000 111 ", "SUPPORT_HOURS": "", "STORAGE_PLACE": "zz-place"})
    assert (got.support_phone, got.support_hours, got.storage_place) == ("000 111", None, "zz-place")


async def test_the_cell_is_public_and_returns_the_cells_values(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {"SUPPORT_PHONE": "000 111 222", "SUPPORT_HOURS": "zz hours", "STORAGE_PLACE": "zz place"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    async with a_client() as client:
        resp = await client.get("/v1/cell")  # no session cookie
    assert resp.status_code == 200
    assert resp.json() == {"support_phone": "000 111 222", "support_hours": "zz hours", "storage_place": "zz place"}


async def test_missing_values_come_back_null(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    async with a_client() as client:
        resp = await client.get("/v1/cell")
    assert resp.status_code == 200
    assert resp.json() == {"support_phone": None, "support_hours": None, "storage_place": None}
