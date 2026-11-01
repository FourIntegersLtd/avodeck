"""Shared test fixtures.

Database and auth tests run against local Supabase (`supabase start`, then `supabase db reset`).
Connection strings come from the environment so CI and other cells can point elsewhere;
the defaults are this checkout's own local Supabase (its port from `supabase status`, so
parallel worktrees never share a database) and the local-only role passwords set in
`supabase/seed.sql`. No real credentials ever appear here.
"""

from __future__ import annotations

import base64
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import psycopg
import pytest

from tests.support.auth import APP_ORIGIN, CSRF_HEADER, AuthAdmin, add_csrf, cookie_header
from tests.support.db import ClientFor
from tests.support.local_supabase import local_db_port, test_dsns
from tests.support.seed import Seed, SeededSession

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.core.db import Database

REPO = Path(__file__).resolve().parents[2]

# Test apps load the launch packs and the fixture packs (`packs/_fixtures/`); a real cell loads only its own.
os.environ.setdefault("PACKS_DIR", os.pathsep.join(str(REPO / p) for p in ("packs", "packs/_fixtures")))
# Session tokens are sealed with the local key provider in tests; a fresh master key each run (never a real key).
os.environ.setdefault("KEY_PROVIDER", "local")
os.environ.setdefault("LOCAL_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
SESSION_SHAPE = ("email", "full_name", "aal", "mfa_age", "idle", "age", "device_label")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def test_database_dsns() -> dict[str, str]:
    """TEST_DATABASE_* from the environment, else this checkout's own local Supabase (`supabase status`)."""
    names = ("TEST_DATABASE_ADMIN_URL", "TEST_DATABASE_URL", "TEST_DATABASE_URL_DIRECT")
    port = "" if all(os.environ.get(n) for n in names) else local_db_port(REPO)
    return test_dsns(port, os.environ)


@pytest.fixture(scope="session")
def admin_dsn(test_database_dsns: dict[str, str]) -> str:
    """Local superuser-ish connection, used only by tests to arrange data."""
    return test_database_dsns["TEST_DATABASE_ADMIN_URL"]


@pytest.fixture(scope="session")
def api_dsn(test_database_dsns: dict[str, str]) -> str:
    return test_database_dsns["TEST_DATABASE_URL"]


@pytest.fixture(scope="session")
def worker_dsn(test_database_dsns: dict[str, str]) -> str:
    return test_database_dsns["TEST_DATABASE_URL_DIRECT"]


@pytest.fixture(scope="session")
def admin(admin_dsn: str) -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=20)
    except psycopg.OperationalError as exc:  # pragma: no cover - environment problem
        pytest.fail(f"local database not reachable; run `supabase start` and `supabase db reset` ({exc.sqlstate})")
    with conn:
        yield conn


# --- core tables, access, HTTP and sessions (stage 1 steps 3 and 4) ------------------------------------------------
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


@pytest.fixture(scope="session")
def auth_env() -> dict[str, str]:
    """Local Supabase Auth: URL and keys from the environment, else from `supabase status` (never printed)."""
    names = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY")
    env = {k: os.environ[k] for k in names if k in os.environ}
    if len(env) < 3:
        out = subprocess.run(
            ["supabase", "status", "-o", "env"], cwd=REPO, capture_output=True, text=True, check=False
        ).stdout
        status = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        env.setdefault("SUPABASE_URL", status.get("API_URL", "http://127.0.0.1:55321").strip('"'))
        env.setdefault("SUPABASE_ANON_KEY", status.get("ANON_KEY", "").strip('"'))
        env.setdefault("SUPABASE_SERVICE_ROLE_KEY", status.get("SERVICE_ROLE_KEY", "").strip('"'))
    if not env["SUPABASE_SERVICE_ROLE_KEY"]:  # pragma: no cover - environment problem
        pytest.fail("local Supabase Auth not reachable; run `supabase start`")
    return {**env, "APP_ORIGIN": APP_ORIGIN}


@pytest.fixture(scope="session")
def auth_admin(auth_env: dict[str, str]) -> AuthAdmin:
    return AuthAdmin(auth_env["SUPABASE_URL"], auth_env["SUPABASE_SERVICE_ROLE_KEY"])


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(database=api_db, region="zz", auth=SupabaseAuth(auth_settings(auth_env)), mailer=OutboxMailer())


@pytest.fixture
async def browser(app: FastAPI) -> AsyncIterator[Callable[[], Awaitable[httpx.AsyncClient]]]:
    """`await browser()` gives a fresh browser: it keeps cookies and copies the CSRF cookie into the header."""
    async with AsyncExitStack() as stack:

        async def make() -> httpx.AsyncClient:
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://test",
                # A browser names its origin on every request, and the API refuses a change without one
                # now that it has an origin of its own (ADR 0031).
                headers={"origin": APP_ORIGIN},
                event_hooks={"request": [add_csrf]},
            )
            return await stack.enter_async_context(client)

        yield make


@pytest.fixture
async def client_with(app: FastAPI) -> AsyncIterator[Callable[..., Awaitable[httpx.AsyncClient]]]:
    """`await client_with(session, csrf=True, **headers)`: a client carrying a seeded session's cookies."""
    async with AsyncExitStack() as stack:

        async def make(session: SeededSession | None, *, csrf: bool = True, **headers: Any) -> httpx.AsyncClient:
            if session is not None:
                headers["cookie"] = cookie_header(session)
                if csrf:
                    headers[CSRF_HEADER] = session.csrf
            headers.setdefault("origin", APP_ORIGIN)  # as a browser does (ADR 0031); pass origin= to change it
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test", headers=headers)
            return await stack.enter_async_context(client)

        yield make


@pytest.fixture
async def client_for(seed: Seed, client_with: Callable[..., Awaitable[httpx.AsyncClient]]) -> ClientFor:
    """`await client_for(person)` gives an HTTP client signed in as that person (None = signed out).

    The person has a real device session (cookie, CSRF token, MFA just completed); keyword arguments
    `email`, `full_name`, `aal`, `mfa_age`, `idle`, `age` shape the session, `csrf=False` leaves the
    CSRF header off, and anything else is sent as a header.
    """

    async def make(person: uuid.UUID | None, *, csrf: bool = True, **kwargs: Any) -> httpx.AsyncClient:
        shape = {k: kwargs.pop(k) for k in SESSION_SHAPE if k in kwargs}
        session = seed.session(person, **shape) if person is not None else None
        return await client_with(session, csrf=csrf, **kwargs)

    return make


# Notification kinds that reach email at once are a short list (app.core.notifications.EMAIL_KINDS). The
# database tests use their own `zz.*` kinds to prove the channel choices, so those kinds are on the list
# for them alone; the unit tests see the real list.
_ZZ_EMAIL_KINDS = frozenset({"zz.held", "zz.org", "zz.other", "zz.ping", "zz.ready"})


@pytest.fixture(autouse=True)
def _zz_kinds_email_in_database_tests(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if {"repository", "http"} & set(request.node.path.parts):
        from app.core import notifications

        monkeypatch.setattr(notifications, "EMAIL_KINDS", notifications.EMAIL_KINDS | _ZZ_EMAIL_KINDS)
