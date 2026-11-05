"""Worker entrypoint: runs the app's job registry from the Postgres queue (`app.core.jobs`).

Each slice registers its job kinds and schedules on `app.core.jobs.registry` in
`app.features.<name>.jobs`; `build_worker()` imports every feature (`app.core.features`), which is
what makes them known to the worker. Jobs reach the mailer as `ctx.mailer` (the local outbox until
the cell has its email service). The worker connects as `app_worker` on the
direct connection (`DATABASE_URL_DIRECT`), never through the pooler and never as the service role.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping

import app.core.housekeeping  # noqa: F401 - registers the foundation's daily schedules
import app.core.notifications  # noqa: F401 - the daily messages schedule
import app.core.uploads  # noqa: F401 - the finalize_upload job
import app.features.pipeline  # noqa: F401 - slice A's job kinds and its DocumentUploaded subscriber
from app.core import metrics
from app.core.config import ConfigError, database_settings, env_int
from app.core.db import Database
from app.core.email import Mailer
from app.core.email_live import mailer_from_env
from app.core.features import DEFAULT_PACKAGE, WORKER_HOOK, load_features, start_features
from app.core.jobs import DEFAULT_SHUTDOWN_GRACE_SECONDS, JobWorker, registry


def build_worker(
    env: Mapping[str, str] | None = None, *, features: str = DEFAULT_PACKAGE, mailer: Mailer | None = None
) -> JobWorker:
    """The worker, not yet connected, knowing every feature's jobs."""
    settings = database_settings(env)
    if not settings.url_direct:
        raise ConfigError("DATABASE_URL_DIRECT is not set")
    load_features(features)
    source = os.environ if env is None else env
    return JobWorker(
        registry,
        database=Database(settings.url_direct, max_size=settings.worker_pool_max),
        queue_pool_max=settings.queue_pool_max,
        dsn=settings.url_direct,
        mailer=mailer if mailer is not None else mailer_from_env(env),
        shutdown_grace_seconds=env_int(
            source, "WORKER_SHUTDOWN_GRACE_SECONDS", DEFAULT_SHUTDOWN_GRACE_SECONDS, minimum=1
        ),
    )


async def run(env: Mapping[str, str] | None = None, *, features: str = DEFAULT_PACKAGE) -> None:
    worker = build_worker(env, features=features)
    source = os.environ if env is None else env
    concurrency = env_int(source, "WORKER_CONCURRENCY", 4, minimum=1)
    await worker.database.open()
    try:
        await start_features(features, worker, hook=WORKER_HOOK)  # each slice's `on_worker_start(worker)`
        async with worker:
            sampling = [
                asyncio.create_task(metrics.sample_loop_lag()),
                asyncio.create_task(metrics.log_snapshots(worker.database)),
            ]
            try:
                await worker.run(concurrency=concurrency, install_signal_handlers=True)
            finally:
                for task in sampling:
                    task.cancel()
    finally:
        await worker.database.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
