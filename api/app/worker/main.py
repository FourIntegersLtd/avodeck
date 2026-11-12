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
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from psycopg_pool import PoolTimeout

import app.core.deferred  # noqa: F401 - the delivery sweep for after-commit work
import app.core.housekeeping  # noqa: F401 - registers the foundation's daily schedules
import app.core.notifications  # noqa: F401 - the daily messages schedule
import app.core.uploads  # noqa: F401 - the finalize_upload job
import app.features.pipeline  # noqa: F401 - slice A's job kinds and its DocumentUploaded subscriber
from app.core import logs, metrics, sandbox
from app.core.case_files import sweep_scratch
from app.core.config import (
    ConfigError,
    database_settings,
    env_int,
    job_max_stall_requeues,
    job_time_limits,
    pipeline_batch_settings,
    queue_retention_days,
)
from app.core.crypto import key_provider_from_env
from app.core.db import Database
from app.core.email import Mailer
from app.core.email_live import mailer_from_env
from app.core.features import DEFAULT_PACKAGE, WORKER_HOOK, load_features, start_features
from app.core.jobs import BULK_QUEUE, DEFAULT_SHUTDOWN_GRACE_SECONDS, INTERACTIVE_QUEUE, JobWorker, registry
from app.core.notify import CaseNotifier
from app.core.packs import default_library
from app.core.scan import scanner_from_env

logger = logging.getLogger("app.worker")


def start_observability(env: Mapping[str, str] | None = None) -> None:
    """Switch on log scrubbing and error reporting in a worker process, as the API does at start-up (rule 6, BG-01,
    BG-02): the same scrubbing record factory with the packs' identifier finders, and Sentry when `SENTRY_DSN` is
    set (environment `SENTRY_ENVIRONMENT`, else the cell's region). Shared with `app.worker.schedules`."""
    source = os.environ if env is None else env
    logs.configure(default_library().identifier_finders())
    dsn = source.get("SENTRY_DSN", "").strip()
    if dsn:
        environment = source.get("SENTRY_ENVIRONMENT", "").strip() or source.get("REGION", "").strip() or "worker"
        logs.init_sentry(dsn, environment=environment)


def check_cell_key(source: Mapping[str, str]) -> None:
    """Deferred work (BG-16) is sealed under the cell key, so the worker needs it as the API does. Missing or wrong
    settings end the process at start-up with the setting's name (never its value)."""
    key_provider_from_env(source)


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
        time_limits=job_time_limits(source),
        queue_retention_days=queue_retention_days(source),
        shutdown_grace_seconds=env_int(
            source, "WORKER_SHUTDOWN_GRACE_SECONDS", DEFAULT_SHUTDOWN_GRACE_SECONDS, minimum=1
        ),
    )


class _Openable(Protocol):
    async def open(self) -> None: ...


async def open_database_with_retry(
    database: _Openable,
    *,
    attempts: int = 40,
    base_delay: float = 5.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Open the pool, waiting out a database that is briefly unreachable.

    Each try already waits psycopg's 30 second pool timeout; the delay between tries doubles from `base_delay`
    up to `max_delay`. With the defaults the worker keeps trying for about 40 minutes before it exits and
    Railway restarts it, so a short outage (8 Nov 2026: nine minutes) no longer uses up the restart budget
    and leaves both workers down. Any other error (wrong role, bad URL) still ends the process at once.
    """
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            await database.open()
        except PoolTimeout as exc:
            if attempt >= attempts:
                raise
            logger.warning(
                "database not reachable (%s), try %d of %d, next in %.0fs", type(exc).__name__, attempt, attempts, delay
            )
            await sleep(delay)
            delay = min(delay * 2, max_delay)
        else:
            return


# Modules each parser child imports as it starts, so no page pays the 2 s import. Named here because the worker is
# what imports the features; `app.core` never does.
SANDBOX_PREWARM = ("app.features.pipeline.parsing",)


async def run(env: Mapping[str, str] | None = None, *, features: str = DEFAULT_PACKAGE) -> None:
    worker = build_worker(env, features=features)
    source = os.environ if env is None else env
    scanner_from_env(source)  # no scanner configured ends the process now, not at the first scan job
    check_cell_key(source)  # no cell key ends it now, not at the first deferred mail
    sweep_scratch()  # plaintext a crashed run left in the scratch directory (DP-06)
    await sandbox.start(prewarm=SANDBOX_PREWARM)  # parser children start now with the parsing modules imported (DP-09)
    concurrency = env_int(source, "WORKER_CONCURRENCY", 4, minimum=1)
    check_pipeline_window(source, concurrency=concurrency)
    await open_database_with_retry(worker.database)
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
        await sandbox.stop()
        if key_listener is not None:
            await key_listener.stop()
        await worker.database.close()


def main() -> None:
    start_observability()
    asyncio.run(run())


if __name__ == "__main__":
    main()
