"""The ops metrics registry: counters and a small ring buffer, numbers only (rule 6)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from app.core import metrics

pytestmark = pytest.mark.anyio

KEYS = {
    "pool_size",
    "pool_available",
    "pool_waiting",
    "loop_lag_ms_p99",
    "provider_429_total",
    "jobs_queued",
    "oldest_queued_seconds",
    "job_seconds_p95",
}


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    metrics.reset()
    yield
    metrics.reset()


def strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from strings(key)
            yield from strings(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            yield from strings(inner)


def test_a_fresh_snapshot_has_every_key_with_zero_values() -> None:
    snap = metrics.snapshot()
    assert set(snap) == KEYS
    assert snap["pool_size"] == snap["pool_available"] == snap["pool_waiting"] == 0
    assert snap["loop_lag_ms_p99"] == 0
    assert snap["provider_429_total"]["bedrock"] == 0
    assert snap["provider_429_total"]["azure_ocr"] == 0
    assert snap["jobs_queued"] == {}


def test_429_counts_by_provider() -> None:
    metrics.count_429("bedrock")
    metrics.count_429("bedrock")
    metrics.count_429("azure_ocr")
    totals = metrics.snapshot()["provider_429_total"]
    assert (totals["bedrock"], totals["azure_ocr"]) == (2, 1)


def test_an_unknown_provider_is_counted_under_other_not_dropped() -> None:
    metrics.count_429("somewhere")
    assert metrics.snapshot()["provider_429_total"]["other"] == 1


def test_loop_lag_is_the_99th_percentile_of_recent_samples() -> None:
    for ms in [1.0] * 98 + [50.0, 400.0]:
        metrics.record_loop_lag_ms(ms)
    assert metrics.snapshot()["loop_lag_ms_p99"] == 50.0


def test_the_ring_buffer_forgets_old_samples() -> None:
    metrics.record_loop_lag_ms(9999.0)
    for _ in range(metrics.RING_SIZE):
        metrics.record_loop_lag_ms(1.0)
    assert metrics.snapshot()["loop_lag_ms_p99"] == 1.0


async def test_the_loop_lag_sampler_records_how_late_it_wakes() -> None:
    task = asyncio.create_task(metrics.sample_loop_lag(interval=0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert metrics.lag_samples() > 0


def test_the_snapshot_holds_numbers_and_short_labels_only() -> None:
    metrics.set_queue_stats({"jobs": (3, 12.5)}, {"split_bundle": 4.0})
    snap = metrics.snapshot()
    assert snap["jobs_queued"] == {"jobs": 3}
    assert snap["oldest_queued_seconds"] == {"jobs": 12.5}
    assert snap["job_seconds_p95"] == {"split_bundle": 4.0}
    assert all(len(s) <= 40 for s in strings(snap)), "no id or text-sized string may appear in the snapshot"


def test_a_label_longer_than_the_limit_is_dropped() -> None:
    metrics.set_queue_stats({"q" * 41: (1, 1.0)}, {"x" * 41: 1.0, "ok_kind": 2.0})
    snap = metrics.snapshot()
    assert snap["job_seconds_p95"] == {"ok_kind": 2.0}
    assert snap["jobs_queued"] == {}
