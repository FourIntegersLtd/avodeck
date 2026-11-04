"""Slice A: the real worker picks a handed-back paused run up (state-machines "Job": paused -> running).

A run handed back by "Try again" is `paused` with the code `resuming`. When the worker picks it up it makes the
paused -> running move, clears the code and does not count a new attempt (attempts count failures and requeues
only). If the cost guard pauses it again it ends `paused` through the worker's own finish, with the guard's code,
and the worker does not put it back on the queue by itself (no resume-later loop).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext
from tests.support.jobs import isolated_registry, open_worker
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


async def test_a_handed_back_run_becomes_running_without_a_new_attempt_and_a_second_cost_guard_ends_it_paused(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()
    seen: list[tuple[Any, ...]] = []

    @registry.kind("zz_guarded")
    async def guarded(ctx: JobContext) -> None:
        row = admin.execute("select status, error_code, attempts from app.jobs where id = %s", (ctx.job_id,)).fetchone()
        assert row is not None
        seen.append((*row, ctx.attempt))
        raise ProcessingPaused("cost_guard")

    row = admin.execute(
        "insert into app.jobs (case_id, kind) values (%s, 'zz_guarded') returning id", (case,)
    ).fetchone()
    assert row is not None
    job: uuid.UUID = row[0]
    admin.execute("update app.jobs set status = 'running', attempts = 1 where id = %s", (job,))
    admin.execute("update app.jobs set status = 'paused', error_code = 'cost_guard' where id = %s", (job,))

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        finished: list[tuple[str, str | None]] = []
        original = worker._finish

        async def spy(case_id: uuid.UUID, job_id: uuid.UUID, status: str, **kwargs: Any) -> None:
            finished.append((status, kwargs.get("error_code")))
            await original(case_id, job_id, status, **kwargs)

        monkeypatch.setattr(worker, "_finish", spy)
        queued = SimpleNamespace(job=SimpleNamespace(attempts=1))  # a stand-in for the queue row's context
        for round_ in (1, 2):
            admin.execute("update app.jobs set error_code = 'resuming' where id = %s", (job,))  # handed back
            await worker._run_job(queued, str(job), str(case))  # type: ignore[arg-type]  # returns: no resume-later
            # While the handler ran: running, the code cleared, the attempt not counted.
            assert seen[-1] == ("running", None, 1, 1), f"round {round_}"
            now = admin.execute("select status, error_code, attempts from app.jobs where id = %s", (job,)).fetchone()
            assert now == ("paused", "cost_guard", 1), f"round {round_}"
    assert finished == [("paused", "cost_guard")] * 2
