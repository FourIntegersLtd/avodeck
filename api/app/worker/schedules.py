"""Run every daily schedule once and exit: `python -m app.worker.schedules` (for a Railway cron service).

It builds the worker as `app.worker.main` does, so every slice's schedules are registered, then calls
`catch_up` for each from its last run to today (UTC). `claim_scheduled_run` runs each date once (F28),
so this is safe beside the worker's own housekeeping loop. Logs hold schedule names and counts only.
Exit 0 when all ran, 1 when any raised (the others still run).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime

from app.core.features import DEFAULT_PACKAGE, WORKER_HOOK, start_features
from app.core.jobs import JobWorker
from app.worker.main import build_worker

logger = logging.getLogger("app.worker.schedules")


async def run_schedules(worker: JobWorker, today: date) -> tuple[dict[str, int], list[str]]:
    """Catch up each schedule; returns how many dates each ran and the names of those that raised."""
    counts: dict[str, int] = {}
    failed: list[str] = []
    for name in sorted(worker.registry.schedules):
        try:
            counts[name] = len(await worker.catch_up(name, today))
        except Exception as exc:  # noqa: BLE001 - one schedule's failure must not stop the others
            logger.error("schedule %s failed: %s", name, type(exc).__name__)
            failed.append(name)
            continue
        logger.info("schedule %s ran %d date(s)", name, counts[name])
    return counts, failed


async def run(env: Mapping[str, str] | None = None, *, features: str = DEFAULT_PACKAGE) -> int:
    worker = build_worker(env, features=features)
    await worker.database.open()
    try:
        await start_features(features, worker, hook=WORKER_HOOK)
        async with worker:
            _, failed = await run_schedules(worker, datetime.now(UTC).date())
    finally:
        await worker.database.close()
    return 1 if failed else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
