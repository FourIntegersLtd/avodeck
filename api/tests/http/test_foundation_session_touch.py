"""Counting activity never keeps the session row locked for the request, and costs no extra connection."""

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

MISSING_CASE = "/v1/cases/00000000-0000-4000-8000-000000000001"


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    return build_app(api_db, auth_env)


@pytest.fixture
def checkouts(api_db: Database, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    taken: list[int] = []
    pool: Any = api_db._pool  # noqa: SLF001
    original = pool.connection

    def counting(*args: Any, **kwargs: Any) -> Any:
        taken.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(pool, "connection", counting)
    return taken


async def test_an_open_request_does_not_keep_its_session_row_locked(
    seed: Seed, api_db: Database, admin: psycopg.Connection
) -> None:
    """Sign-out and MFA verify write the session row: they must not wait behind a long request."""
    from app.core.auth import session as sessions

    person = seed.person()
    session = seed.session(person, idle=timedelta(minutes=5))
    args = sessions.resolve_args(session.token, touch=True)
    async with api_db.session_request(args, "a") as (_, first):
        assert first is not None
        admin.execute("set lock_timeout = '1s'")
        try:
            admin.execute("update app.sessions set aal = aal where id = %s", (session.id,))
        finally:
            admin.execute("reset lock_timeout")


async def test_activity_is_counted_when_the_request_ends(
    seed: Seed, api_db: Database, admin: psycopg.Connection
) -> None:
    from app.core.auth import session as sessions

    person = seed.person()
    session = seed.session(person, idle=timedelta(minutes=5))
    args = sessions.resolve_args(session.token, touch=True)
    async with api_db.session_request(args, "a") as (_, row):
        assert row is not None
    seen = admin.execute(
        "select now() - last_seen_at < interval '1 minute' from app.sessions where id = %s", (session.id,)
    ).fetchone()
    assert seen is not None and seen[0]


async def test_a_session_that_does_not_count_activity_is_left_alone(
    seed: Seed, api_db: Database, admin: psycopg.Connection
) -> None:
    from app.core.auth import session as sessions

    person = seed.person()
    session = seed.session(person, idle=timedelta(minutes=5))
    async with api_db.session_request(sessions.resolve_args(session.token, touch=False), "a") as (_, row):
        assert row is not None
    seen = admin.execute(
        "select now() - last_seen_at > interval '4 minutes' from app.sessions where id = %s", (session.id,)
    ).fetchone()
    assert seen is not None and seen[0]


async def test_an_error_answer_takes_one_connection_and_still_counts(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, checkouts: list[int]
) -> None:
    person = seed.person()
    client = await client_for(person, idle=timedelta(minutes=5))
    before = admin.execute("select last_seen_at from app.sessions where person_id = %s", (person,)).fetchone()
    checkouts.clear()
    resp = await client.get(MISSING_CASE)
    assert resp.status_code == 404
    assert len(checkouts) == 1
    after = admin.execute("select last_seen_at from app.sessions where person_id = %s", (person,)).fetchone()
    assert before and after and after[0] > before[0]


async def test_a_fresh_session_takes_one_connection_on_an_error_answer(
    client_for: ClientFor, seed: Seed, checkouts: list[int]
) -> None:
    person = seed.person()
    client = await client_for(person)  # last seen just now: nothing to count
    checkouts.clear()
    resp = await client.get(MISSING_CASE)
    assert resp.status_code == 404
    assert len(checkouts) == 1


async def test_activity_is_still_counted_when_the_client_goes_away_mid_request(
    seed: Seed, api_db: Database, admin: psycopg.Connection
) -> None:
    """A disconnect cancels the request; the count (and the rollback before it) must still finish."""
    import anyio

    from app.core.auth import session as sessions

    person = seed.person()
    session = seed.session(person, idle=timedelta(minutes=5))
    with anyio.CancelScope() as scope:
        async with api_db.session_request(sessions.resolve_args(session.token, touch=True), "a") as (_, row):
            assert row is not None
            scope.cancel()
            await anyio.sleep(1)
    assert scope.cancelled_caught
    seen = admin.execute(
        "select now() - last_seen_at < interval '1 minute' from app.sessions where id = %s", (session.id,)
    ).fetchone()
    assert seen is not None and seen[0]


def test_touch_does_not_revive_a_session_past_its_idle_or_total_age(seed: Seed, admin: psycopg.Connection) -> None:
    person = seed.person()
    idle = seed.session(person, idle=timedelta(hours=2))
    old = seed.session(person, idle=timedelta(minutes=5), age=timedelta(days=60))
    fresh = seed.session(person, idle=timedelta(minutes=5))
    for s in (idle, old, fresh):
        admin.execute("select app.session_touch(%s, interval '30 minutes', interval '12 hours')", (s.id,))
    ages = {
        s.id: admin.execute(
            "select now() - last_seen_at > interval '1 minute' from app.sessions where id = %s", (s.id,)
        ).fetchone()
        for s in (idle, old, fresh)
    }
    assert ages[idle.id] == (True,), "an idle-expired session was revived"
    assert ages[old.id] == (True,), "a session past its total age was revived"
    assert ages[fresh.id] == (False,)
