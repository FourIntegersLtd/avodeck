"""Shared test fixtures.

Database tests run against local Supabase (`supabase start`, then `supabase db reset`).
Connection strings come from the environment so CI and other cells can point elsewhere;
the defaults are the local-only Supabase ports and the local-only role passwords set in
`supabase/seed.sql`. No real credentials ever appear here.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

import httpx
import psycopg
import pytest

from tests.support.db import ClientFor
from tests.support.seed import Seed

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.core.db import Database

LOCAL_HOST = "127.0.0.1:55322/postgres"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _dsn(env: str, default: str) -> str:
    return os.environ.get(env, default)


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    """Local superuser-ish connection, used only by tests to arrange data."""
    return _dsn("TEST_DATABASE_ADMIN_URL", f"postgresql://postgres:postgres@{LOCAL_HOST}")


@pytest.fixture(scope="session")
def api_dsn() -> str:
    return _dsn("TEST_DATABASE_URL", f"postgresql://app_api:app_api_local@{LOCAL_HOST}")


@pytest.fixture(scope="session")
def worker_dsn() -> str:
    return _dsn("TEST_DATABASE_URL_DIRECT", f"postgresql://app_worker:app_worker_local@{LOCAL_HOST}")


@pytest.fixture(scope="session")
def admin(admin_dsn: str) -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:  # pragma: no cover - environment problem
        pytest.fail(f"local database not reachable; run `supabase start` and `supabase db reset` ({exc.sqlstate})")
    with conn:
        yield conn


# --- core tables, access and HTTP (stage 1 step 3) ------------------------------------------------
# App modules are imported inside the fixtures so a missing module fails only the tests that need it.


@pytest.fixture
def seed(admin: psycopg.Connection) -> Seed:
    return Seed(admin)


@pytest.fixture
async def api_db(api_dsn: str) -> AsyncIterator[Database]:
    from app.core.db import Database

    database = Database(api_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def app(api_db: Database) -> FastAPI:
    from app.main import create_app
    from tests.support.identity import header_identity

    return create_app(database=api_db, identity_provider=header_identity, region="zz")


@pytest.fixture
async def client_for(app: FastAPI) -> AsyncIterator[ClientFor]:
    """`await client_for(person)` gives an HTTP client signed in as that person (None = signed out)."""
    from tests.support.identity import PERSON_HEADER

    async with AsyncExitStack() as stack:

        async def make(person: uuid.UUID | None, **headers: Any) -> httpx.AsyncClient:
            if person is not None:
                headers[PERSON_HEADER] = str(person)
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)
            return await stack.enter_async_context(client)

        yield make
