"""Server-Timing and the statement count on the request line (measuring slow calls, rule 6 kept)."""

from __future__ import annotations

import logging
import re
import uuid

import pytest
from fastapi import FastAPI

from app.core.db import Database
from tests.support.db import ClientFor
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    return build_app(api_db, auth_env)


async def test_today_has_server_timing_and_a_query_count_in_the_log_without_ids(
    client_for: ClientFor, seed: Seed, caplog: pytest.LogCaptureFixture
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    seed.case(org, expert, status="active")
    client = await client_for(expert)
    caplog.set_level(logging.INFO, logger="app.request")
    resp = await client.get("/v1/today")
    assert resp.status_code == 200, resp.text
    timing = resp.headers["server-timing"]
    match = re.fullmatch(r'app;dur=\d+(\.\d+)?, db;desc="queries=(\d+)"', timing)
    assert match and int(match.group(2)) > 0
    lines = [r.getMessage() for r in caplog.records if r.name == "app.request" and "/v1/today" in r.getMessage()]
    assert len(lines) == 1
    assert re.search(r"queries=\d+", lines[0])
    assert not re.search(r"[0-9a-f]{8}-", caplog.text)
    assert str(uuid.UUID(int=0))[:8] not in lines[0]
