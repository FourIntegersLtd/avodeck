"""The job queue (Procrastinate on Postgres, architecture 2 and 7; foundation brief "Shared code").

- Only one case-changing job runs per case at a time; other cases, and jobs that only read, run alongside.
- Interactive jobs run before batch jobs.
- The worker acts as `app_worker` for the job's own case only (ADR 0013).
- Progress (`pct`, `step`, `eta_seconds`) goes on the `jobs` row; status follows the state machine.
- A job that keeps failing ends `failed` with an error code after its attempts; one that pauses ends `paused`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import BATCH_PRIORITY, INTERACTIVE_PRIORITY, JobContext
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

Cases = tuple[uuid.UUID, uuid.UUID, uuid.UUID]


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=6)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def expert_cases(seed: Seed) -> Cases:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    return expert, seed.case(org, expert), seed.case(org, expert)


async def test_only_one_case_changing_job_runs_per_case_at_a_time(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    expert_cases: Cases,
) -> None:
    expert, case_a, case_b = expert_cases
    registry = isolated_registry()
    spans: dict[uuid.UUID, list[float]] = {}

    async def work(ctx: JobContext) -> None:
        spans[ctx.job_id] = [time.monotonic()]
        await asyncio.sleep(0.4)
        spans[ctx.job_id].append(time.monotonic())

    registry.kind("zz_change")(work)
    registry.kind("zz_look", changes_case=False)(work)

    async with api_db.request(scope_for(expert)) as conn:
        a1 = await registry.enqueue(conn, case_a, "zz_change")
        a2 = await registry.enqueue(conn, case_a, "zz_change")
        b1 = await registry.enqueue(conn, case_b, "zz_change")
        a_look = await registry.enqueue(conn, case_a, "zz_look")

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        result = await run_until_finished(worker, admin, [a1, a2, b1, a_look], concurrency=4)
    assert set(result.values()) == {"done"}

    def overlap(x: uuid.UUID, y: uuid.UUID) -> bool:
        return spans[x][0] < spans[y][1] and spans[y][0] < spans[x][1]

    assert not overlap(a1, a2), "two case-changing jobs ran on one case at once"
    assert overlap(a1, b1) or overlap(a2, b1), "another case's job had to wait"
    assert overlap(a1, a_look) or overlap(a2, a_look), "a job that does not change the case had to wait"


async def test_interactive_jobs_run_before_batch_jobs(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    expert_cases: Cases,
) -> None:
    expert, case_a, case_b = expert_cases
    registry = isolated_registry()
    order: list[str] = []

    @registry.kind("zz_batch")
    async def batch(ctx: JobContext) -> None:
        order.append("batch")

    @registry.kind("zz_interactive", interactive=True)
    async def interactive(ctx: JobContext) -> None:
        order.append("interactive")

    async with api_db.request(scope_for(expert)) as conn:
        later = await registry.enqueue(conn, case_a, "zz_batch")
        sooner = await registry.enqueue(conn, case_b, "zz_interactive")

    priorities: dict[uuid.UUID, int] = dict(
        admin.execute("select id, priority from app.jobs where id = any(%s)", ([later, sooner],)).fetchall()
    )
    assert priorities == {later: BATCH_PRIORITY, sooner: INTERACTIVE_PRIORITY}
    assert INTERACTIVE_PRIORITY > BATCH_PRIORITY

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_finished(worker, admin, [later, sooner], concurrency=1)
    assert order == ["interactive", "batch"]


async def test_a_job_reports_progress_and_sees_only_its_own_case(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    expert_cases: Cases,
) -> None:
    expert, case_a, _ = expert_cases
    registry = isolated_registry()
    seen: dict[str, object] = {}

    @registry.kind("zz_progress")
    async def progress(ctx: JobContext) -> None:
        await ctx.progress(40, eta_seconds=12, step="split")
        seen["row"] = admin.execute(
            "select status, pct, step, eta_seconds from app.jobs where id = %s", (ctx.job_id,)
        ).fetchone()
        async with ctx.transaction() as conn:
            seen["cases"] = await (await conn.execute("select id from app.cases")).fetchall()

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case_a, "zz_progress")
    queued = admin.execute("select status, attempts, kind, case_id from app.jobs where id = %s", (job,)).fetchone()
    assert queued == ("queued", 0, "zz_progress", case_a)

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_finished(worker, admin, [job])

    assert seen["row"] == ("running", 40, "split", 12)
    assert seen["cases"] == [(case_a,)], "a job's transaction sees only its own case"
    row = admin.execute(
        "select status, pct, attempts, started_at is not null, finished_at is not null, error_code"
        " from app.jobs where id = %s",
        (job,),
    ).fetchone()
    assert row == ("done", 100, 1, True, True, None)


async def test_a_job_records_the_code_version_it_was_queued_under(
    api_db: Database,
    admin: psycopg.Connection,
    expert_cases: Cases,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Architecture 10.9: every job row names the code version that queued it."""
    expert, case_a, _ = expert_cases
    registry = isolated_registry()

    @registry.kind("zz_versioned")
    async def versioned(ctx: JobContext) -> None:
        return None

    monkeypatch.setenv("CODE_VERSION", "build-1234")
    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case_a, "zz_versioned")
    row = admin.execute("select code_version from app.jobs where id = %s", (job,)).fetchone()
    assert row == ("build-1234",)


async def test_a_job_that_keeps_failing_ends_failed_with_an_error_code_after_its_attempts(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    expert_cases: Cases,
) -> None:
    expert, case_a, _ = expert_cases
    registry = isolated_registry()
    calls: list[int] = []

    @registry.kind("zz_broken")
    async def broken(ctx: JobContext) -> None:
        calls.append(ctx.attempt)
        raise RuntimeError("synthetic failure")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case_a, "zz_broken")
    async with open_worker(registry, worker_db, worker_dsn, max_attempts=3) as worker:
        await run_until_finished(worker, admin, [job])
    assert calls == [1, 2, 3]
    row = admin.execute("select status, attempts, error_code from app.jobs where id = %s", (job,)).fetchone()
    assert row == ("failed", 3, "job_error")


async def test_a_retried_job_never_passes_through_failed(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    expert_cases: Cases,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure with attempts left goes running -> queued in one step: nothing reading in between sees a
    finally-failed job (the UI's "Try again", a test waiting for the end)."""
    expert, case_a, _ = expert_cases
    registry = isolated_registry()
    calls: list[int] = []
    finished_with: list[str] = []

    @registry.kind("zz_flaky")
    async def flaky(ctx: JobContext) -> None:
        calls.append(ctx.attempt)
        if ctx.attempt == 1:
            raise RuntimeError("synthetic failure")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case_a, "zz_flaky")
    async with open_worker(registry, worker_db, worker_dsn, max_attempts=3) as worker:
        original = worker._finish

        async def spy(case: uuid.UUID, job_id: uuid.UUID, status: str, **kwargs: Any) -> None:
            finished_with.append(status)
            await original(case, job_id, status, **kwargs)

        monkeypatch.setattr(worker, "_finish", spy)
        await run_until_finished(worker, admin, [job])
    assert calls == [1, 2]
    assert finished_with == ["done"]
    row = admin.execute("select status, error_code, finished_at from app.jobs where id = %s", (job,)).fetchone()
    assert row is not None and row[0] == "done" and row[2] is not None


async def test_a_job_that_pauses_ends_paused_with_its_reason_and_is_not_retried(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    expert_cases: Cases,
) -> None:
    expert, case_a, _ = expert_cases
    registry = isolated_registry()
    calls: list[int] = []

    @registry.kind("zz_paused")
    async def paused(ctx: JobContext) -> None:
        calls.append(ctx.attempt)
        raise ProcessingPaused("cost_guard")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case_a, "zz_paused")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_finished(worker, admin, [job])
    assert calls == [1]
    row = admin.execute("select status, error_code from app.jobs where id = %s", (job,)).fetchone()
    assert row == ("paused", "cost_guard")


async def test_a_kind_the_worker_does_not_know_fails_without_running_anything(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    expert_cases: Cases,
) -> None:
    expert, case_a, _ = expert_cases
    sender, receiver = isolated_registry(), isolated_registry()
    receiver.queue = sender.queue

    @sender.kind("zz_elsewhere")
    async def elsewhere(ctx: JobContext) -> None:  # pragma: no cover - never runs
        raise AssertionError

    async with api_db.request(scope_for(expert)) as conn:
        job = await sender.enqueue(conn, case_a, "zz_elsewhere")
    async with open_worker(receiver, worker_db, worker_dsn) as worker:
        await run_until_finished(worker, admin, [job])
    assert admin.execute("select status, error_code from app.jobs where id = %s", (job,)).fetchone() == (
        "failed",
        "unknown_kind",
    )


async def test_only_someone_who_may_upload_to_the_case_can_queue_its_jobs(
    api_db: Database, seed: Seed, expert_cases: Cases
) -> None:
    _, case_a, _ = expert_cases
    outsider = seed.person()
    registry = isolated_registry()

    @registry.kind("zz_any")
    async def anything(ctx: JobContext) -> None:  # pragma: no cover - never runs
        raise AssertionError

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with api_db.request(scope_for(outsider)) as conn:
            await registry.enqueue(conn, case_a, "zz_any")
    with pytest.raises(KeyError):
        async with api_db.request(scope_for(outsider)) as conn:
            await registry.enqueue(conn, case_a, "zz_not_registered")
