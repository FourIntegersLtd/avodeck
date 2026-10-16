"""Worker entrypoint: runs the app's job registry from the Postgres queue (`app.core.jobs`).

Each slice registers its job kinds on `app.core.jobs.registry`; importing `app.features` here (as
slices arrive) is what makes them known to the worker. The worker connects as `app_worker` on the
direct connection (`DATABASE_URL_DIRECT`), never through the pooler and never as the service role.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping

import app.core.housekeeping  # noqa: F401 - registers the foundation's daily schedules
from app.core.config import ConfigError, database_settings
from app.core.db import Database
from app.core.jobs import JobWorker, registry


def build_worker(env: Mapping[str, str] | None = None) -> JobWorker:
    """The worker, not yet connected."""
    settings = database_settings(env)
    if not settings.url_direct:
        raise ConfigError("DATABASE_URL_DIRECT is not set")
    return JobWorker(registry, database=Database(settings.url_direct), dsn=settings.url_direct)


async def run(env: Mapping[str, str] | None = None) -> None:
    worker = build_worker(env)
    source = os.environ if env is None else env
    concurrency = int(source.get("WORKER_CONCURRENCY", "4"))
    await worker.database.open()
    try:
        async with worker:
            await worker.run(concurrency=concurrency, install_signal_handlers=True)
    finally:
        await worker.database.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
