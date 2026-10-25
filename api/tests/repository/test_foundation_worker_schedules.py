"""`python -m app.worker.schedules`: run every registered daily schedule once and exit (F28).

A cron service runs it beside the worker. `claim_scheduled_run` makes each date run once, so the
worker's own housekeeping loop and this entry point can both run without doing the work twice.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import psycopg
import pytest

from app.core.db import Database
from app.worker.schedules import run_schedules
from tests.support.jobs import isolated_registry, open_worker

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


async def test_every_schedule_runs_once_per_date_and_a_second_run_does_nothing(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    registry = isolated_registry()
    name = f"zz_cron_{uuid.uuid4().hex[:8]}"
    ran: list[date] = []

    @registry.schedule(name)
    async def handler(conn: psycopg.AsyncConnection, day: date) -> None:
        ran.append(day)

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        today = date.today()
        assert await run_schedules(worker, today) == ({name: 1}, [])
        assert ran == [today]
        assert await run_schedules(worker, today) == ({name: 0}, [])
        assert ran == [today]


async def test_a_failing_schedule_is_reported_and_the_others_still_run(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    registry = isolated_registry()
    good, bad = f"zz_good_{uuid.uuid4().hex[:8]}", f"zz_bad_{uuid.uuid4().hex[:8]}"
    ran: list[date] = []

    @registry.schedule(bad)
    async def broken(conn: psycopg.AsyncConnection, day: date) -> None:
        raise RuntimeError("patient text must never be logged")

    @registry.schedule(good)
    async def fine(conn: psycopg.AsyncConnection, day: date) -> None:
        ran.append(day)

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        counts, failed = await run_schedules(worker, date.today())
    assert failed == [bad]
    assert counts[good] == 1 and ran == [date.today()]
