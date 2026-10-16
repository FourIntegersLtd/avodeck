"""Shared test fixtures.

Database tests run against local Supabase (`supabase start`, then `supabase db reset`).
Connection strings come from the environment so CI and other cells can point elsewhere;
the defaults are the local-only Supabase ports and the local-only role passwords set in
`supabase/seed.sql`. No real credentials ever appear here.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

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
