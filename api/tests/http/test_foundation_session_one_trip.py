"""A signed-in request checks out one database connection: the session lookup shares the request's transaction."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import psycopg
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


@pytest.fixture
def checkouts(api_db: Database, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """One entry per connection taken from the pool."""
    taken: list[int] = []
    pool: Any = api_db._pool  # noqa: SLF001
    original = pool.connection

    def counting(*args: Any, **kwargs: Any) -> Any:
        taken.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(pool, "connection", counting)
    return taken


async def test_today_takes_one_connection_from_the_pool(
    client_for: ClientFor, seed: Seed, checkouts: list[int]
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    seed.case(org, expert, status="active")
    client = await client_for(expert)
    checkouts.clear()
    resp = await client.get("/v1/today")
    assert resp.status_code == 200, resp.text
    assert len(checkouts) == 1


async def test_an_expired_session_is_revoked_even_though_the_answer_is_401(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, checkouts: list[int]
) -> None:
    person = seed.person()
    session = seed.session(person, idle=timedelta(minutes=31))
    client = await client_for(person, idle=timedelta(minutes=31))
    resp = await client.get("/v1/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_expired"
    revoked = admin.execute("select revoked_reason from app.sessions where person_id = %s", (person,)).fetchall()
    assert sorted(r[0] for r in revoked if r[0]) == ["idle"]
    assert session.id is not None


async def test_a_refused_csrf_still_counts_as_activity(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    person = seed.person()
    client = await client_for(person, csrf=False, idle=timedelta(minutes=5))
    before = admin.execute("select last_seen_at from app.sessions where person_id = %s", (person,)).fetchone()
    resp = await client.post("/v1/auth/mfa/recovery-codes")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_required"
    after = admin.execute("select last_seen_at from app.sessions where person_id = %s", (person,)).fetchone()
    assert before and after and after[0] > before[0]
