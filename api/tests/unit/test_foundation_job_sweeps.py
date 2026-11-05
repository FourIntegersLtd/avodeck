"""Features register sweeps that run in the worker's housekeeping (page-batch jobs, ADR 0038).

`@registry.sweep(name)` takes a handler `(conn)` that runs on an anonymous worker connection once per
housekeeping round, beside `requeue_stalled` and `requeue_orphaned`. A sweep that fails logs only the exception
type (rule 6) and neither stops the other sweeps nor the rest of the round.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import pytest
from psycopg import AsyncConnection

from app.core.db import Database
from app.core.jobs import JobRegistry
from tests.support.jobs import isolated_registry, open_worker

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def test_a_sweep_name_can_be_registered_once() -> None:
    registry = JobRegistry()

    @registry.sweep("zz_sweep")
    async def first(conn: AsyncConnection) -> None:
        return None

    with pytest.raises(ValueError, match="registered twice"):

        @registry.sweep("zz_sweep")
        async def second(conn: AsyncConnection) -> None:
            return None


async def test_every_registered_sweep_runs_on_an_anonymous_connection(worker_db: Database, worker_dsn: str) -> None:
    registry = isolated_registry()
    seen: list[tuple[str, str | None]] = []

    @registry.sweep("zz_first")
    async def first(conn: AsyncConnection) -> None:
        cur = await conn.execute("select nullif(current_setting('app.job_case_id', true), '')")
        row = await cur.fetchone()
        seen.append(("first", row[0] if row else "none"))

    @registry.sweep("zz_second")
    async def second(conn: AsyncConnection) -> None:
        seen.append(("second", None))

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert await worker.run_sweeps() == 2
    assert seen == [("first", None), ("second", None)], "no case context: a sweep sets its own, transaction-locally"


async def test_a_failing_sweep_logs_only_its_type_and_the_others_still_run(
    worker_db: Database, worker_dsn: str, caplog: pytest.LogCaptureFixture
) -> None:
    registry = isolated_registry()
    ran: list[str] = []

    @registry.sweep("zz_broken")
    async def broken(conn: AsyncConnection) -> None:
        raise ValueError("Jane Doe, born 1 January 1970")  # patient text must never reach a log

    @registry.sweep("zz_fine")
    async def fine(conn: AsyncConnection) -> None:
        ran.append("fine")

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        with caplog.at_level(logging.ERROR, logger="app.jobs"):
            assert await worker.run_sweeps() == 1
    assert ran == ["fine"]
    text = caplog.text
    assert "ValueError" in text and "zz_broken" in text
    assert "Jane" not in text and "1970" not in text


async def test_the_housekeeping_round_runs_the_sweeps(worker_db: Database, worker_dsn: str) -> None:
    registry = isolated_registry()
    rounds: list[int] = []

    @registry.sweep("zz_each_round")
    async def each_round(conn: AsyncConnection) -> None:
        rounds.append(1)

    async with open_worker(registry, worker_db, worker_dsn, stalled_after_seconds=0.2) as worker:
        task = asyncio.create_task(worker.run())
        try:
            for _ in range(100):
                if len(rounds) >= 2:
                    break
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    assert len(rounds) >= 2, "once per housekeeping round, every round"
