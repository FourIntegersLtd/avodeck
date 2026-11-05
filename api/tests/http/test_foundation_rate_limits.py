"""Per-person, per-endpoint rate limits (scalability PR 12).

A signed-in person over `API_RATE_PER_MINUTE` on one endpoint gets 429 `slow_down` with `Retry-After`; another
person, and another endpoint, are unaffected. The count is one upsert in the request's own transaction, so it
never takes a second connection (no hold-and-wait on a small pool) and a refused request is not counted again.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from tests.support.auth import APP_ORIGIN, CSRF_HEADER, cookie_header
from tests.support.db import ClientFor
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    app = build_app(api_db, auth_env)
    app.state.api_rate_per_minute = 3
    return app


async def _statuses(client: httpx.AsyncClient, path: str, n: int) -> list[int]:
    return [(await client.get(path)).status_code for _ in range(n)]


async def test_a_person_over_the_limit_gets_429_slow_down_with_retry_after(client_for: ClientFor, seed: Seed) -> None:
    client = await client_for(seed.person())
    assert await _statuses(client, "/v1/me", 3) == [200, 200, 200]
    resp = await client.get("/v1/me")
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "slow_down"
    assert 1 <= int(resp.headers["retry-after"]) <= 60


async def test_the_refusal_says_nothing_but_a_fixed_message(client_for: ClientFor, seed: Seed) -> None:
    client = await client_for(seed.person())
    await _statuses(client, "/v1/me", 3)
    body = (await client.get("/v1/me")).json()["error"]
    assert set(body) == {"code", "message"}
    assert "/v1" not in body["message"]


async def test_another_person_is_unaffected(client_for: ClientFor, seed: Seed) -> None:
    busy = await client_for(seed.person())
    other = await client_for(seed.person())
    await _statuses(busy, "/v1/me", 4)
    assert (await other.get("/v1/me")).status_code == 200


async def test_each_endpoint_counts_on_its_own(client_for: ClientFor, seed: Seed) -> None:
    client = await client_for(seed.person())
    await _statuses(client, "/v1/me", 4)
    assert (await client.get("/v1/today")).status_code == 200


async def test_a_new_minute_lets_the_person_in_again(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    person = seed.person()
    client = await client_for(person)
    await _statuses(client, "/v1/me", 4)
    admin.execute(
        "update app.rate_limit_hits set window_start = window_start - interval '1 minute' where person_id = %s",
        (person,),
    )
    assert (await client.get("/v1/me")).status_code == 200


async def test_a_limit_of_zero_is_no_limit(client_for: ClientFor, seed: Seed, app: FastAPI) -> None:
    app.state.api_rate_per_minute = 0
    client = await client_for(seed.person())
    assert set(await _statuses(client, "/v1/me", 8)) == {200}


async def test_answers_that_are_errors_still_count(client_for: ClientFor, seed: Seed) -> None:
    """A signed-in person probing ids that are not theirs (404) is limited like any other; a failed request's
    transaction rolls back, so the count is kept in a short transaction of its own."""
    client = await client_for(seed.person())
    missing = f"/v1/cases/{uuid.uuid4()}/events"
    assert await _statuses(client, missing, 3) == [404, 404, 404]
    assert (await client.get(missing)).status_code == 429


async def test_a_refused_request_is_not_counted_again(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    person = seed.person()
    client = await client_for(person)
    await _statuses(client, "/v1/me", 6)
    assert admin.execute("select hits from app.rate_limit_hits where person_id = %s", (person,)).fetchone() == (3,)


async def test_the_count_is_committed_with_the_request(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    person = seed.person()
    client = await client_for(person)
    await _statuses(client, "/v1/me", 2)
    rows = admin.execute("select endpoint, hits from app.rate_limit_hits where person_id = %s", (person,)).fetchall()
    assert rows == [("GET /v1/me", 2)]


async def test_signed_out_routes_are_not_counted(client_for: ClientFor, app: FastAPI) -> None:
    client = await client_for(None)
    assert set(await _statuses(client, "/v1/cell", 6)) == {200}


@pytest.fixture
async def one_connection(api_dsn: str) -> AsyncIterator[Database]:
    database = Database(api_dsn, max_size=1, acquire_timeout=0.5)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


async def test_counting_never_needs_a_second_connection(
    one_connection: Database, auth_env: dict[str, str], seed: Seed
) -> None:
    """Both the count and the recount of a failed request run with a pool of one: no hold-and-wait."""
    app = build_app(one_connection, auth_env)
    app.state.api_rate_per_minute = 3
    session = seed.session(seed.person())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        headers={"cookie": cookie_header(session), CSRF_HEADER: session.csrf, "origin": APP_ORIGIN},
    ) as client:
        assert (await client.get("/v1/me")).status_code == 200
        assert (await client.get(f"/v1/cases/{uuid.uuid4()}/events")).status_code == 404
        statuses = await _statuses(client, f"/v1/cases/{uuid.uuid4()}/events", 4)
    assert statuses[-1] == 429
