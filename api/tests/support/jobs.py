"""Running the job worker in a test until given jobs have finished."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

import psycopg

if TYPE_CHECKING:
    from app.core.db import Database
    from app.core.jobs import JobRegistry, JobWorker

FINISHED = ("done", "failed", "paused")


def isolated_registry() -> JobRegistry:
    """A registry on its own queue, so a test's worker never picks up another test's jobs."""
    from app.core.jobs import JobRegistry

    return JobRegistry(queue=f"test_{uuid.uuid4().hex[:12]}")


@contextlib.asynccontextmanager
async def open_worker(registry: JobRegistry, database: Database, dsn: str, **options: Any) -> AsyncIterator[JobWorker]:
    from app.core.jobs import JobWorker

    worker = JobWorker(registry, database=database, dsn=dsn, poll_seconds=0.05, retry_wait_seconds=0, **options)
    async with worker:
        yield worker


def statuses(admin: psycopg.Connection, jobs: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    rows = admin.execute("select id, status from app.jobs where id = any(%s)", (list(jobs),)).fetchall()
    return {r[0]: r[1] for r in rows}


async def run_until_finished(
    worker: JobWorker,
    admin: psycopg.Connection,
    jobs: Sequence[uuid.UUID],
    *,
    concurrency: int = 1,
    timeout: float = 30,
) -> dict[uuid.UUID, str]:
    task = asyncio.create_task(worker.run(concurrency=concurrency))
    deadline = time.monotonic() + timeout
    try:
        while True:
            found = statuses(admin, jobs)
            if len(found) == len(jobs) and all(s in FINISHED for s in found.values()):
                return found
            if task.done():
                task.result()
                raise AssertionError(f"worker stopped early: {found}")
            if time.monotonic() > deadline:
                raise AssertionError(f"jobs not finished in {timeout}s: {found}")
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
