"""Failure mode F5 for jobs (docs/spec/failure-modes.md), owned by the foundation.

F5: the reading service (Bedrock, Azure) is throttled or down. The AI kit backs off inside the call,
then raises `ProcessingPaused` (`service_busy`, `model_unavailable`). The job is `paused` and put
back on the queue after an exponential back-off; it carries on when the service answers. A job
still paused after 2 hours of back-off ends `failed` with the pause reason, so a person can retry it
(failed → queued). Pauses that wait for a person (cost guard K3, a cancelled run) are not resumed.

Every status change goes through `running` (paused → running → failed), as the state machine allows.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Sequence

import psycopg
import pytest

from app.core.ai.kit import MAX_WAIT_SECONDS
from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import DEFAULT_PAUSE_DELAYS, RESUMABLE_PAUSES, JobContext, JobRegistry, JobWorker
from app.core.state_machines import JOB_STATUS_TRANSITIONS
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=6)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def _status(admin: psycopg.Connection, job: uuid.UUID) -> str:
    row = admin.execute("select status from app.jobs where id = %s", (job,)).fetchone()
    assert row is not None
    return str(row[0])


async def _run_until(
    workers: Sequence[JobWorker],
    admin: psycopg.Connection,
    job: uuid.UUID,
    ends: tuple[str, ...] = ("done", "failed"),
    *,
    concurrency: int = 1,
    timeout: float = 30,
    seen: list[str] | None = None,
) -> str:
    """Run the workers until the job ends; `seen` collects each status change the poll observes."""
    tasks = [asyncio.create_task(w.run(concurrency=concurrency)) for w in workers]
    deadline = time.monotonic() + timeout
    try:
        while True:
            status = _status(admin, job)
            if seen is not None and (not seen or seen[-1] != status):
                seen.append(status)
            if status in ends:
                return status
            for task in tasks:
                if task.done():
                    task.result()
                    raise AssertionError(f"worker stopped early: {status}")
            if time.monotonic() > deadline:
                raise AssertionError(f"job not finished in {timeout}s: {status}")
            await asyncio.sleep(0.02)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _todo(admin: psycopg.Connection, job: uuid.UUID) -> list[tuple[str, int]]:
    """The queue's rows for this job: status and attempts."""
    return [
        (str(r[0]), int(r[1]))
        for r in admin.execute(
            "select status::text, attempts from procrastinate.procrastinate_jobs where args->>'job_id' = %s",
            (str(job),),
        ).fetchall()
    ]


async def _enqueue(api_db: Database, seed: Seed, registry: JobRegistry, kind: str) -> uuid.UUID:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    async with api_db.request(scope_for(expert)) as conn:
        return await registry.enqueue(conn, case, kind)


def test_f5_the_default_back_off_is_exponential_and_spends_at_most_two_hours() -> None:
    delays = list(DEFAULT_PAUSE_DELAYS)
    assert len(delays) >= 3
    assert all(later >= earlier for earlier, later in zip(delays, delays[1:], strict=False)), "back-off must grow"
    assert delays[1] == 2 * delays[0], "back-off must be exponential"
    assert sum(delays) == MAX_WAIT_SECONDS == 2 * 60 * 60
    assert {"service_busy", "model_unavailable"} <= RESUMABLE_PAUSES
    assert not {"cost_guard", "cancelled"} & RESUMABLE_PAUSES, "these wait for a person"


async def test_f5_a_paused_job_is_requeued_after_its_back_off_and_finishes_when_the_service_answers(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    calls: list[float] = []

    @registry.kind("zz_busy_once")
    async def busy_once(ctx: JobContext) -> None:
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise ProcessingPaused("service_busy")

    job = await _enqueue(api_db, seed, registry, "zz_busy_once")
    async with open_worker(registry, worker_db, worker_dsn, pause_delays=(30.0,)) as worker:
        assert await _run_until([worker], admin, job, ("paused",)) == "paused"
        # Paused with its reason, and back on the queue for later, not dropped.
        row = admin.execute("select error_code, finished_at from app.jobs where id = %s", (job,)).fetchone()
        assert row == ("service_busy", None)
        waiting = admin.execute(
            "select status::text, scheduled_at > now() + interval '20 seconds'"
            " from procrastinate.procrastinate_jobs where args->>'job_id' = %s",
            (str(job),),
        ).fetchall()
        assert waiting == [("todo", True)], "a paused job must be queued again after its back-off"

    calls.clear()
    job = await _enqueue(api_db, seed, registry, "zz_busy_once")
    async with open_worker(registry, worker_db, worker_dsn, pause_delays=(0.5,)) as worker:
        assert await _run_until([worker], admin, job) == "done"

    assert len(calls) == 2
    assert calls[1] - calls[0] >= 0.5, "resumed before its back-off"
    row = admin.execute("select status, error_code, pct, attempts from app.jobs where id = %s", (job,)).fetchone()
    assert row == ("done", None, 100, 1), "a pause is not a failed attempt"


async def test_f5_a_job_keeps_backing_off_while_the_service_is_still_busy(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    calls: list[float] = []

    @registry.kind("zz_busy_twice")
    async def busy_twice(ctx: JobContext) -> None:
        calls.append(time.monotonic())
        if len(calls) <= 2:
            raise ProcessingPaused("model_unavailable" if len(calls) == 1 else "service_busy")

    job = await _enqueue(api_db, seed, registry, "zz_busy_twice")
    async with open_worker(registry, worker_db, worker_dsn, pause_delays=(0.3, 0.9, 5.0)) as worker:
        assert await _run_until([worker], admin, job) == "done"

    assert len(calls) == 3
    assert calls[1] - calls[0] >= 0.3
    assert calls[2] - calls[1] >= 0.9, "the second wait must follow the next, longer back-off"


@pytest.mark.parametrize("reason", ["service_busy", "model_unavailable"])
async def test_f5_a_job_still_paused_after_the_limit_fails_with_its_reason(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed, reason: str
) -> None:
    registry = isolated_registry()
    calls: list[int] = []

    @registry.kind("zz_always_busy")
    async def always_busy(ctx: JobContext) -> None:
        calls.append(ctx.attempt)
        raise ProcessingPaused(reason)

    job = await _enqueue(api_db, seed, registry, "zz_always_busy")
    async with open_worker(registry, worker_db, worker_dsn, pause_delays=(0.0, 0.0)) as worker:
        assert await _run_until([worker], admin, job) == "failed"

    assert len(calls) == 3, "one run, then one per back-off"
    row = admin.execute("select error_code, finished_at is not null from app.jobs where id = %s", (job,)).fetchone()
    assert row == (reason, True)
    assert [s for s, _ in _todo(admin, job)] == ["succeeded"], "nothing left on the queue"
    # The resume path: a failed job can be put back on the queue (failed → queued).
    assert "queued" in JOB_STATUS_TRANSITIONS["failed"]
    admin.execute("update app.jobs set status = 'queued' where id = %s", (job,))


@pytest.mark.parametrize("reason", ["cost_guard", "cancelled"])
async def test_a_pause_that_waits_for_a_person_is_not_resumed(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed, reason: str
) -> None:
    registry = isolated_registry()
    calls: list[int] = []

    @registry.kind("zz_held")
    async def held(ctx: JobContext) -> None:
        calls.append(1)
        raise ProcessingPaused(reason)

    job = await _enqueue(api_db, seed, registry, "zz_held")
    async with open_worker(registry, worker_db, worker_dsn, pause_delays=(0.0,)) as worker:
        assert await _run_until([worker], admin, job, ("paused",)) == "paused"
        await asyncio.sleep(0.5)

    assert calls == [1]
    assert _status(admin, job) == "paused"
    assert [s for s, _ in _todo(admin, job)] == ["succeeded"]


async def test_f5_a_paused_job_is_never_run_twice_at_once(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    running = 0
    most = 0
    calls = 0

    # A kind that only reads has no case lock, so nothing but the queue keeps its runs apart.
    @registry.kind("zz_reader_busy", changes_case=False)
    async def reader_busy(ctx: JobContext) -> None:
        nonlocal running, most, calls
        running += 1
        calls += 1
        most = max(most, running)
        try:
            await asyncio.sleep(0.1)
            if calls <= 3:
                raise ProcessingPaused("service_busy")
        finally:
            running -= 1

    job = await _enqueue(api_db, seed, registry, "zz_reader_busy")
    async with (
        open_worker(registry, worker_db, worker_dsn, pause_delays=(0.0, 0.0, 0.0, 0.0)) as one,
        open_worker(registry, worker_db, worker_dsn, pause_delays=(0.0, 0.0, 0.0, 0.0)) as two,
    ):
        assert await _run_until([one, two], admin, job, concurrency=3) == "done"

    assert calls == 4
    assert most == 1, "a paused job ran twice at once"
    assert len(_todo(admin, job)) == 1, "a resumed job must reuse its queue row, never add a second"


async def test_f5_every_status_change_of_a_paused_job_is_allowed_by_the_state_machine(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()

    @registry.kind("zz_slow_busy")
    async def slow_busy(ctx: JobContext) -> None:
        await asyncio.sleep(0.3)
        raise ProcessingPaused("service_busy")

    job = await _enqueue(api_db, seed, registry, "zz_slow_busy")
    seen: list[str] = []
    async with open_worker(registry, worker_db, worker_dsn, pause_delays=(0.3, 0.3)) as worker:
        assert await _run_until([worker], admin, job, seen=seen) == "failed"

    assert seen[0] == "queued" and seen[-1] == "failed"
    assert "paused" in seen
    for before, after in zip(seen, seen[1:], strict=False):
        assert after in JOB_STATUS_TRANSITIONS[before], f"{before} → {after} is not in state-machines.md"
