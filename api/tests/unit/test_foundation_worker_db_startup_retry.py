"""The worker waits for the database at start-up instead of crashing.

On 8 November 2026 the pooler was unreachable for nine minutes; both workers tried once, hit psycopg's
30 second pool timeout, exited, used up Railway's restart budget and stayed down until someone redeployed.
The API survived because its listener reconnects. The worker now does the same at start-up.
"""

from __future__ import annotations

import logging

import pytest
from psycopg_pool import PoolTimeout

from app.worker.main import open_database_with_retry

pytestmark = pytest.mark.anyio


class FakeDatabase:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def open(self) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise PoolTimeout("pool initialization incomplete after 30.0 sec")


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


async def test_a_short_outage_delays_the_start_but_does_not_end_it(caplog: pytest.LogCaptureFixture) -> None:
    database = FakeDatabase(failures=3)
    sleep = FakeSleep()
    with caplog.at_level(logging.WARNING, logger="app.worker"):
        await open_database_with_retry(database, attempts=10, base_delay=5.0, sleep=sleep)
    assert database.calls == 4
    assert sleep.delays == [5.0, 10.0, 20.0]  # doubles each time
    assert all("PoolTimeout" in record.message for record in caplog.records)
    assert len(caplog.records) == 3


async def test_the_wait_between_tries_is_capped() -> None:
    database = FakeDatabase(failures=6)
    sleep = FakeSleep()
    await open_database_with_retry(database, attempts=10, base_delay=5.0, max_delay=30.0, sleep=sleep)
    assert sleep.delays == [5.0, 10.0, 20.0, 30.0, 30.0, 30.0]


async def test_a_long_outage_still_ends_the_process() -> None:
    database = FakeDatabase(failures=99)
    sleep = FakeSleep()
    with pytest.raises(PoolTimeout):
        await open_database_with_retry(database, attempts=4, base_delay=1.0, sleep=sleep)
    assert database.calls == 4
    assert len(sleep.delays) == 3


async def test_a_healthy_database_opens_at_once() -> None:
    database = FakeDatabase(failures=0)
    sleep = FakeSleep()
    await open_database_with_retry(database, sleep=sleep)
    assert database.calls == 1
    assert sleep.delays == []


async def test_other_errors_are_not_retried() -> None:
    class Broken(FakeDatabase):
        async def open(self) -> None:
            self.calls += 1
            raise RuntimeError("worker may not run as a privileged role")

    database = Broken(failures=0)
    with pytest.raises(RuntimeError):
        await open_database_with_retry(database, sleep=FakeSleep())
    assert database.calls == 1
