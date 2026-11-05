"""A busy pool or a runaway statement answers 503 `busy` with Retry-After, never a hang or a 500."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.db import Database
from tests.support.auth import APP_ORIGIN, CSRF_HEADER, cookie_header
from tests.support.db import scope_for
from tests.support.seed import Seed


@pytest.fixture
async def small_db(api_dsn: str) -> AsyncIterator[Database]:
    database = Database(api_dsn, max_size=1, acquire_timeout=0.3, statement_timeout_ms=300)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def small_app(small_db: Database, auth_env: dict[str, str]) -> FastAPI:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    app = create_app(database=small_db, region="zz", auth=SupabaseAuth(auth_settings(auth_env)), mailer=OutboxMailer())

    @app.get("/v1/_test_slow")
    async def slow() -> dict[str, Any]:
        async with small_db.request(scope_for(uuid.uuid4())) as conn:
            await conn.execute("select pg_sleep(3)")
        return {}

    return app


@pytest.mark.anyio
async def test_a_busy_pool_answers_503_busy_with_retry_after(
    small_app: FastAPI, small_db: Database, seed: Seed
) -> None:
    session = seed.session(seed.person())
    async with small_db.request(scope_for(uuid.uuid4())):  # holds the only connection
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=small_app),
            base_url="https://test",
            headers={"cookie": cookie_header(session), CSRF_HEADER: session.csrf, "origin": APP_ORIGIN},
        ) as client:
            resp = await client.get("/v1/me")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "busy"
    assert resp.headers["retry-after"] == "2"


@pytest.mark.anyio
async def test_a_statement_over_the_limit_answers_503_busy_not_500(small_app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=small_app), base_url="https://test", headers={"origin": APP_ORIGIN}
    ) as client:
        resp = await client.get("/v1/_test_slow")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "busy"
    assert resp.headers["retry-after"] == "2"
