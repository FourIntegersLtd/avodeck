"""Operational metrics for GET /v1/internal/metrics: counters and small ring buffers kept in this process.

Numbers and short labels only (rule 6): a queue name or job kind is a code, never an id or text, and a label
over `MAX_LABEL` characters is dropped. No new dependency.

Each process keeps its own numbers: the API's endpoint shows the API's pool and event-loop lag, its own
provider 429s, and the queue and job run-time numbers read from the database (shared by both services).
The worker logs its own snapshot every `LOG_SECONDS` (`log_snapshot`), also numbers only.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.db import Database

logger = logging.getLogger(__name__)

RING_SIZE = 512
MAX_LABEL = 40
QUEUE_CACHE_SECONDS = 10.0
LOG_SECONDS = 60.0
PROVIDERS = ("bedrock", "anthropic", "azure_ocr")

_lock = threading.Lock()
_lag_ms: deque[float] = deque(maxlen=RING_SIZE)
_pool: dict[str, int] = {}
_totals: dict[str, int] = {}
_queued: dict[str, int] = {}
_oldest: dict[str, float] = {}
_job_p95: dict[str, float] = {}
_queue_read_at: float | None = None


def reset() -> None:
    """Forget everything (tests)."""
    global _queue_read_at
    with _lock:
        _lag_ms.clear()
        for table in (_pool, _totals, _queued, _oldest, _job_p95):
            table.clear()
        _queue_read_at = None


def count_429(provider: str) -> None:
    """A provider answered 429 (too many requests)."""
    key = provider if provider in PROVIDERS else "other"
    with _lock:
        _totals[key] = _totals.get(key, 0) + 1


def record_loop_lag_ms(ms: float) -> None:
    with _lock:
        _lag_ms.append(ms)


def lag_samples() -> int:
    with _lock:
        return len(_lag_ms)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))  # nearest rank
    return ordered[min(rank, len(ordered)) - 1]


def _short(labels: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in labels.items() if len(k) <= MAX_LABEL}


def set_queue_stats(queues: dict[str, tuple[int, float]], job_p95: dict[str, float]) -> None:
    """Queue depth and oldest wait (seconds) per queue; 95th percentile run time (seconds) per job kind."""
    with _lock:
        _queued.clear()
        _oldest.clear()
        _job_p95.clear()
        for name, (count, oldest) in _short(queues).items():
            _queued[name] = int(count)
            _oldest[name] = round(float(oldest), 1)
        for kind, seconds in _short(job_p95).items():
            _job_p95[kind] = round(float(seconds), 2)


def set_pool_stats(stats: dict[str, int]) -> None:
    with _lock:
        _pool.clear()
        _pool.update(stats)


def snapshot() -> dict[str, Any]:
    """The current numbers: plain ints, floats and short labels."""
    with _lock:
        return {
            "pool_size": _pool.get("pool_size", 0),
            "pool_available": _pool.get("pool_available", 0),
            "pool_waiting": _pool.get("requests_waiting", 0),
            "loop_lag_ms_p99": round(_percentile(list(_lag_ms), 0.99), 1),
            "provider_429_total": {**dict.fromkeys((*PROVIDERS, "other"), 0), **_totals},
            "jobs_queued": dict(_queued),
            "oldest_queued_seconds": dict(_oldest),
            "job_seconds_p95": dict(_job_p95),
        }


async def sample_loop_lag(interval: float = 0.5) -> None:
    """Sleep `interval` seconds over and over, recording how late each wake-up is. Runs until cancelled."""
    loop = asyncio.get_running_loop()
    while True:
        expected = loop.time() + interval
        await asyncio.sleep(interval)
        record_loop_lag_ms(max(0.0, (loop.time() - expected) * 1000))


async def refresh_queue_stats(database: Database, *, force: bool = False) -> None:
    """Read queue depth, oldest wait and job run times from the database, at most every `QUEUE_CACHE_SECONDS`."""
    global _queue_read_at
    now = time.monotonic()
    if not force and _queue_read_at is not None and now - _queue_read_at < QUEUE_CACHE_SECONDS:
        return
    _queue_read_at = now  # set first: a failing read is not retried on every request
    try:
        async with database.anonymous() as conn:
            cur = await conn.execute("select queue_name, queued, oldest_seconds from app.ops_queue_stats()")
            queues = {str(r[0]): (int(r[1]), float(r[2])) for r in await cur.fetchall()}
            cur = await conn.execute("select kind, p95_seconds from app.ops_job_seconds()")
            job_p95 = {str(r[0]): float(r[1]) for r in await cur.fetchall()}
    except Exception as exc:  # noqa: BLE001 - keep the last numbers; the type only, never the message
        logger.warning("queue metrics read failed: %s", type(exc).__name__)
        return
    set_queue_stats(queues, job_p95)


async def collect(database: Database, *, force: bool = False) -> dict[str, Any]:
    """Refresh the pool and (cached) queue numbers, then return the snapshot."""
    set_pool_stats(database.pool_stats())
    await refresh_queue_stats(database, force=force)
    return snapshot()


async def log_snapshots(database: Database, interval: float = LOG_SECONDS) -> None:
    """For the worker: log the snapshot as numbers every `interval` seconds. Runs until cancelled."""
    while True:
        await asyncio.sleep(interval)
        try:
            logger.info("metrics %s", await collect(database))
        except Exception as exc:  # noqa: BLE001 - never stop the worker for a number
            logger.warning("metrics snapshot failed: %s", type(exc).__name__)
