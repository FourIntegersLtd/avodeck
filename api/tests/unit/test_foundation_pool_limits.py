"""core/db.py fails fast instead of hanging: a busy pool answers `busy` quickly (PR 1 of the scalability plan)."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from app.core.config import database_settings
from app.core.db import Database
from app.core.errors import PoolBusy
from tests.support.db import scope_for


async def _open(dsn: str, **kwargs: object) -> Database:
    database = Database(dsn, **kwargs)  # type: ignore[arg-type]
    await database.open()
    return database


@pytest.mark.anyio
async def test_a_busy_pool_raises_pool_busy_within_the_acquire_timeout(api_dsn: str) -> None:
    database = await _open(api_dsn, max_size=1, acquire_timeout=0.3)
    try:
        async with database.request(scope_for(uuid.uuid4())):
            started = time.monotonic()
            with pytest.raises(PoolBusy):
                async with database.request(scope_for(uuid.uuid4())):
                    pass
            assert time.monotonic() - started < 0.3 + 0.5
    finally:
        await database.close()


@pytest.mark.anyio
async def test_the_other_ways_in_are_also_fast(api_dsn: str) -> None:
    database = await _open(api_dsn, max_size=1, acquire_timeout=0.2)
    try:
        async with database.request(scope_for(uuid.uuid4())):
            with pytest.raises(PoolBusy):
                async with database.anonymous("r"):
                    pass
            with pytest.raises(PoolBusy):
                await database.anonymous_one("select 1")
            with pytest.raises(PoolBusy):
                async with database.job(uuid.uuid4()):
                    pass
    finally:
        await database.close()


@pytest.mark.anyio
async def test_too_many_waiting_is_refused_at_once(api_dsn: str) -> None:
    database = await _open(api_dsn, max_size=1, acquire_timeout=5.0, max_waiting=1)
    try:
        async with database.request(scope_for(uuid.uuid4())):

            async def waiter() -> None:
                async with database.anonymous("w"):
                    pass

            first = asyncio.create_task(waiter())
            await asyncio.sleep(0.1)  # the first waiter is in the queue
            started = time.monotonic()
            with pytest.raises(PoolBusy):
                await waiter()
            assert time.monotonic() - started < 0.5
            first.cancel()
    finally:
        await database.close()


@pytest.mark.anyio
async def test_a_statement_over_the_limit_is_cancelled_as_busy(api_dsn: str) -> None:
    database = await _open(api_dsn, max_size=1, statement_timeout_ms=200)
    try:
        with pytest.raises(PoolBusy):
            async with database.request(scope_for(uuid.uuid4())) as conn:
                await conn.execute("select pg_sleep(3)")
        # the connection went back clean
        async with database.request(scope_for(uuid.uuid4())) as conn:
            cur = await conn.execute("select 1")
            assert await cur.fetchone() == (1,)
    finally:
        await database.close()


@pytest.mark.anyio
async def test_without_a_statement_timeout_a_slow_statement_finishes(worker_dsn: str) -> None:
    database = await _open(worker_dsn, max_size=1)
    try:
        async with database.job(uuid.uuid4()) as conn:
            await conn.execute("select pg_sleep(0.6)")
    finally:
        await database.close()


def test_the_worker_database_has_no_statement_timeout_and_waits_for_a_connection() -> None:
    from app.worker.main import build_worker

    worker = build_worker(
        {
            "DATABASE_URL": "postgresql://app_api:x@db.example:6543/postgres",
            "DATABASE_URL_DIRECT": "postgresql://app_worker:x@db.example:5432/postgres",
        }
    )
    assert worker.database.statement_timeout_ms == 0
    assert worker.database._pool.timeout == 30.0  # noqa: SLF001
    assert worker.database._pool.max_waiting == 0  # noqa: SLF001


def test_the_api_limits_have_defaults_and_can_be_set() -> None:
    base = {"DATABASE_URL": "postgresql://app_api:x@db.example:6543/postgres"}
    default = database_settings(base)
    assert default.acquire_timeout_seconds == 2.0
    assert default.max_waiting == 50
    assert default.statement_timeout_ms == 5000
    custom = database_settings(
        {
            **base,
            "API_DB_ACQUIRE_TIMEOUT_SECONDS": "1.5",
            "API_DB_MAX_WAITING": "7",
            "API_DB_STATEMENT_TIMEOUT_MS": "0",
        }
    )
    assert (custom.acquire_timeout_seconds, custom.max_waiting, custom.statement_timeout_ms) == (1.5, 7, 0)


def test_the_api_database_is_built_from_those_settings() -> None:
    from app.main import api_database

    settings = database_settings(
        {
            "DATABASE_URL": "postgresql://app_api:x@db.example:6543/postgres",
            "API_DB_ACQUIRE_TIMEOUT_SECONDS": "1.5",
            "API_DB_MAX_WAITING": "7",
            "API_DB_STATEMENT_TIMEOUT_MS": "1234",
        }
    )
    database = api_database(settings)
    assert database._pool.timeout == 1.5  # noqa: SLF001
    assert database._pool.max_waiting == 7  # noqa: SLF001
    assert database.statement_timeout_ms == 1234
