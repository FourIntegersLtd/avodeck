"""Worker entrypoint: runs the app's job registry from the Postgres queue (`app.core.jobs`).

Each slice registers its job kinds and schedules on `app.core.jobs.registry` in
`app.features.<name>.jobs`; `build_worker()` imports every feature (`app.core.features`), which is
what makes them known to the worker. Jobs reach the mailer as `ctx.mailer` (the local outbox until
the cell has its email service). The worker connects as `app_worker` on the
direct connection (`DATABASE_URL_DIRECT`), never through the pooler and never as the service role.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping

import app.core.housekeeping  # noqa: F401 - registers the foundation's daily schedules
import app.core.notifications  # noqa: F401 - the daily messages schedule
import app.core.uploads  # noqa: F401 - the finalize_upload job
import app.features.pipeline  # noqa: F401 - slice A's job kinds and its DocumentUploaded subscriber
from app.core import metrics
from app.core.config import ConfigError, database_settings, env_int, job_max_stall_requeues, pipeline_batch_settings
from app.core.db import Database
from app.core.email import Mailer
from app.core.email_live import mailer_from_env
from app.core.features import DEFAULT_PACKAGE, WORKER_HOOK, load_features, start_features
from app.core.jobs import BULK_QUEUE, DEFAULT_SHUTDOWN_GRACE_SECONDS, INTERACTIVE_QUEUE, JobWorker, registry
from app.core.notify import CaseNotifier

logger = logging.getLogger("app.worker")


def worker_queues(source: Mapping[str, str]) -> list[str] | None:
    """`WORKER_QUEUES`: a comma list of `interactive` and/or `bulk`. Unset takes every queue. The bulk worker
    also drains the old `jobs` queue (pipeline retries and work queued before the split still use it)."""
    raw = source.get("WORKER_QUEUES", "").strip()
    if not raw:
        return None
    names = [n.strip() for n in raw.split(",") if n.strip()]
    known = {INTERACTIVE_QUEUE: registry.interactive_queue, BULK_QUEUE: registry.bulk_queue}
    unknown = [n for n in names if n not in known]
    if unknown or not names:
        raise ConfigError(f"WORKER_QUEUES must list {INTERACTIVE_QUEUE} and/or {BULK_QUEUE}, separated by commas")
    chosen = [known[n] for n in dict.fromkeys(names)]
    return chosen + [registry.queue] if BULK_QUEUE in names else chosen


def check_pipeline_window(source: Mapping[str, str], *, concurrency: int) -> str | None:
    """Warn when page batching is on and its window leaves no slot for the parent run (ADR 0038): the window must be
    at most `WORKER_CONCURRENCY - 1`. Returns the warning, or None. Never refuses to start."""
    settings = pipeline_batch_settings(source)
    if not settings.enabled or settings.window <= concurrency - 1:
        return None
    message = (
        f"PIPELINE_BATCH_WINDOW is above WORKER_CONCURRENCY - 1: window {settings.window}, concurrency {concurrency};"
        " children can fill every slot and starve the parent"
    )
    logger.warning(message)
    return message


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
        queues=worker_queues(source),
        dsn=settings.url_direct,
        mailer=mailer if mailer is not None else mailer_from_env(env),
        max_stall_requeues=job_max_stall_requeues(source),
        shutdown_grace_seconds=env_int(
            source, "WORKER_SHUTDOWN_GRACE_SECONDS", DEFAULT_SHUTDOWN_GRACE_SECONDS, minimum=1
        ),
    )


async def run(env: Mapping[str, str] | None = None, *, features: str = DEFAULT_PACKAGE) -> None:
    worker = build_worker(env, features=features)
    source = os.environ if env is None else env
    concurrency = env_int(source, "WORKER_CONCURRENCY", 4, minimum=1)
    check_pipeline_window(source, concurrency=concurrency)
    await worker.database.open()
    # One LISTEN connection (on the direct URL) so a case key destroyed in another process leaves this
    # process's key cache at once, not after 5 minutes (PR 15); without it the cache is checked every 30 seconds.
    # Without the URL no listener is started and the 30 second check bounds the cache, so the worker still runs.
    listen_url = source.get("DATABASE_URL_DIRECT", "").strip()
    key_listener = CaseNotifier(listen_url) if listen_url else None
    if key_listener is not None:
        await key_listener.start()
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
        if key_listener is not None:
            await key_listener.stop()
        await worker.database.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
