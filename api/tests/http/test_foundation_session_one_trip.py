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


async def test_a_dead_session_leaves_nobody_signed_in_on_the_connection(seed: Seed, api_db: Database) -> None:
    from app.core.auth import session as sessions

    person = seed.person()
    live = seed.session(person)
    dead = seed.session(person, idle=timedelta(minutes=31))
    for cookie, who in ((live, str(person)), (dead, None)):
        async with api_db.session_request(sessions.resolve_args(cookie.token, touch=True), "r") as (conn, row):
            assert row is not None
            seen = await (await conn.execute("select app.current_person_id()::text")).fetchone()
            assert seen is not None and seen[0] == who


async def test_requests_with_one_cookie_do_not_wait_for_each_other(
    client_for: ClientFor, seed: Seed, api_db: Database
) -> None:
    from app.core.auth import session as sessions

    person = seed.person()
    session = seed.session(person, idle=timedelta(minutes=5))
    args = sessions.resolve_args(session.token, touch=True)
    async with api_db.session_request(args, "a") as (_, first):
        assert first is not None
        # While the first request is still open, a second one gets through at once (no lock on the session row).
        import asyncio

        async with asyncio.timeout(3), api_db.session_request(args, "b") as (_, second):
            assert second is not None


async def test_an_error_answer_keeps_the_activity_but_not_the_work(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    person = seed.person()
    client = await client_for(person, idle=timedelta(minutes=5))
    before = admin.execute("select last_seen_at from app.sessions where person_id = %s", (person,)).fetchone()
    resp = await client.get("/v1/cases/00000000-0000-4000-8000-000000000001")
    assert resp.status_code == 404
    after = admin.execute("select last_seen_at from app.sessions where person_id = %s", (person,)).fetchone()
    assert before and after and after[0] > before[0]


async def test_a_refused_password_change_still_counts_as_activity(
    seed: Seed, client_with: Any, admin: psycopg.Connection
) -> None:
    person = seed.person()
    admin.execute("update app.people set must_change_password = true where id = %s", (person,))
    session = seed.session(person, idle=timedelta(minutes=5))
    resp = await (await client_with(session)).get("/v1/today")
    assert resp.json()["error"]["code"] == "password_change_required"
    row = admin.execute("select last_seen_at from app.sessions where id = %s", (session.id,)).fetchone()
    assert row is not None
    seen = admin.execute("select now() - %s < interval '1 minute'", (row[0],)).fetchone()
    assert seen is not None and seen[0]
