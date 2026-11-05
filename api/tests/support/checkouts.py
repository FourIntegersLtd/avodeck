"""Counting the pool checkouts a capped job holds (page-batch jobs, ADR 0038).

`CheckoutSpy(database).install(monkeypatch)` wraps the database's transactions. It counts only those opened
while a per-job limiter is set (`ctx.limit_checkouts`), so the worker's own checkouts, which are never capped, are
left out. `peak` is the most held at once; `nested` counts checkouts taken by a task that already held one (the
shape that could deadlock the limiter: a task must never wait for a permit while holding one).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.core.db import Database, job_checkouts


class CheckoutSpy:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.in_flight = 0
        self.peak = 0
        self.nested = 0
        self.total = 0
        self._holding: set[asyncio.Task[Any] | None] = set()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> CheckoutSpy:
        original = self.database._transaction

        @contextlib.asynccontextmanager
        async def spied(settings: dict[str, str]) -> AsyncIterator[Any]:
            if job_checkouts.get() is None:
                async with original(settings) as conn:
                    yield conn
                return
            task = asyncio.current_task()
            if task in self._holding:
                self.nested += 1
            self._holding.add(task)
            self.in_flight += 1
            self.total += 1
            self.peak = max(self.peak, self.in_flight)
            try:
                async with original(settings) as conn:
                    yield conn
            finally:
                self.in_flight -= 1
                self._holding.discard(task)

        monkeypatch.setattr(self.database, "_transaction", spied)
        return self
