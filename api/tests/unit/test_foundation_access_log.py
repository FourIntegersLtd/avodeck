"""The access log: one line per request with method, route template, status and time (rule 6).

It is on once the app is made (not left to the root logger's level) and it never carries the raw path,
the query string, ids or bodies.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from app.main import create_app, mount

pytestmark = pytest.mark.anyio

CASE = uuid.uuid4()
SECRET = "Pemberton-zz-secret"


def an_app() -> FastAPI:
    auth = SimpleNamespace(
        settings=SimpleNamespace(
            app_origin="https://zz.invalid", app_origins=("https://zz.invalid",), cookie_domain=None
        )
    )
    return create_app(database=SimpleNamespace(), region="zz", auth=auth)  # type: ignore[arg-type]


def a_client() -> httpx.AsyncClient:
    app = an_app()
    router = APIRouter()

    @router.get("/v1/test-only/cases/{case_id}/things")
    async def things(case_id: uuid.UUID) -> dict[str, str]:
        return {"ok": "yes"}

    mount(app, router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def test_the_request_log_is_on_without_any_logging_set_up_by_the_test() -> None:
    an_app()
    log = logging.getLogger("app.request")
    assert log.isEnabledFor(logging.INFO)
    assert log.handlers, "a handler of its own, so the lines are written even with no other log setup"


async def test_a_line_has_method_route_status_and_time_and_nothing_else(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.request")
    async with a_client() as client:
        resp = await client.get(f"/v1/test-only/cases/{CASE}/things", params={"q": SECRET, "patient": "x"})
    assert resp.status_code == 200
    [record] = [r for r in caplog.records if r.name == "app.request"]
    line = record.getMessage()
    assert line.startswith("GET /v1/test-only/cases/{case_id}/things 200 ")
    assert line.endswith("ms")
    assert str(CASE) not in caplog.text and SECRET not in caplog.text and "?" not in caplog.text


async def test_an_unknown_path_is_logged_as_unmatched(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.request")
    async with a_client() as client:
        await client.get(f"/v1/nothing/{CASE}", params={"q": SECRET})
    [record] = [r for r in caplog.records if r.name == "app.request"]
    assert record.getMessage().startswith("GET unmatched 404 ")
    assert str(CASE) not in caplog.text and SECRET not in caplog.text
