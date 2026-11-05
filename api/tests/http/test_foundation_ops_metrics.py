"""GET /v1/internal/metrics: numbers for the operators, behind the OPS_METRICS_TOKEN bearer token."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.core import metrics
from app.core.db import Database

pytestmark = pytest.mark.anyio

TOKEN = "ops-token-synthetic-0123456789"


@pytest.fixture
def ops_app(api_db: Database) -> FastAPI:
    from types import SimpleNamespace

    from app.main import create_app

    auth = SimpleNamespace(
        settings=SimpleNamespace(app_origin="https://test", app_origins=("https://test",), cookie_domain="test")
    )
    return create_app(database=api_db, region="zz", auth=auth)  # type: ignore[arg-type]


def client(app: FastAPI, **headers: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test", headers=headers)


async def test_without_the_token_setting_the_endpoint_does_not_exist(
    ops_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPS_METRICS_TOKEN", raising=False)
    async with client(ops_app, authorization=f"Bearer {TOKEN}") as c:
        assert (await c.get("/v1/internal/metrics")).status_code == 404


async def test_no_header_or_a_wrong_token_is_404(ops_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_METRICS_TOKEN", TOKEN)
    async with client(ops_app) as c:
        assert (await c.get("/v1/internal/metrics")).status_code == 404
    async with client(ops_app, authorization="Bearer wrong") as c:
        assert (await c.get("/v1/internal/metrics")).status_code == 404
    async with client(ops_app, authorization=TOKEN) as c:
        assert (await c.get("/v1/internal/metrics")).status_code == 404


async def test_the_right_token_returns_live_numbers(ops_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_METRICS_TOKEN", TOKEN)
    metrics.reset()
    metrics.count_429("bedrock")
    async with client(ops_app, authorization=f"Bearer {TOKEN}") as c:
        resp = await c.get("/v1/internal/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider_429_total"]["bedrock"] == 1
    assert body["pool_size"] >= 1, "pool numbers are read live from the database pool"
    assert body["pool_available"] + 0 <= body["pool_size"]
    assert {"jobs_queued", "oldest_queued_seconds", "job_seconds_p95", "loop_lag_ms_p99", "pool_waiting"} <= set(body)
    assert resp.headers["cache-control"] == "no-store"
    metrics.reset()
