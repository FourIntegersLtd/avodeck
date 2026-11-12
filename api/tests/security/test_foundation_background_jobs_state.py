"""Background jobs, audit 15, area C: status moves, one-job-at-a-time per case, and the cancel flag.

13 (failure recovery) is covered by `tests/guards/test_foundation_job_failure_modes.py` (F6: a crashed worker's job is
requeued and skips the steps it finished) and `tests/repository/test_foundation_job_stall_cap.py` (stall cap and the
dead letter).
"""

from __future__ import annotations

import asyncio
import itertools
import time
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.jobs import JobContext
from app.features.pipeline import repo
from tests.support.background_jobs import attempt, role_connection
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

STATUSES = ("queued", "running", "done", "failed", "paused")
ALLOWED = {
    ("queued", "running"),
    ("running", "done"),
    ("running", "failed"),
    ("failed", "queued"),
    ("running", "paused"),
    ("paused", "running"),
}
PATH_TO = {  # the moves the guard allows, to reach each starting status
    "queued": [],
    "running": ["running"],
    "done": ["running", "done"],
    "failed": ["running", "failed"],
    "paused": ["running", "paused"],
}


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=8)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


# --- 10. the guard ---------------------------------------------------------------------------------------------------


def _job_in(admin: psycopg.Connection, seed: Seed, case: uuid.UUID, status: str) -> uuid.UUID:
    job = seed.job(case)
    for step in PATH_TO[status]:
        admin.execute("update app.jobs set status = %s where id = %s", (step, job))
    assert admin.execute("select status from app.jobs where id = %s", (job,)).fetchone() == (status,)
    return job


@pytest.mark.parametrize(("old", "new"), list(itertools.product(STATUSES, STATUSES)))
@pytest.mark.parametrize("actor", ["app_worker", "admin"])
def test_the_jobs_guard_allows_exactly_the_documented_status_moves(
    old: str, new: str, actor: str, seed: Seed, admin: psycopg.Connection, worker_dsn: str, admin_dsn: str
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = _job_in(admin, seed, case, old)
    with role_connection(worker_dsn if actor == "app_worker" else admin_dsn) as conn:
        if actor == "app_worker":
            conn.execute("select set_config('app.job_case_id', %s, true)", (str(case),))
        code, rows, _ = attempt(conn, "update app.jobs set status = %s where id = %s", (new, job))
        after = conn.execute("select status from app.jobs where id = %s", (job,)).fetchone()  # before the rollback
    permitted = old == new or (old, new) in ALLOWED
    if permitted:
        assert (code, rows) == (None, 1), f"{old} -> {new} was refused ({code})"
    else:
        assert code is not None, f"{old} -> {new} was accepted"
    assert after == (new if permitted else old,)


@pytest.mark.parametrize("actor", ["app_worker", "admin"])
def test_a_same_status_update_of_a_running_job_is_not_refused_by_the_guard(
    actor: str, seed: Seed, admin: psycopg.Connection, worker_dsn: str, admin_dsn: str
) -> None:
    """running -> running goes through. BG-05 relies on this: a second queue row for a running job is not stopped by
    the status guard, so only the worker's own check (which skips `done` but not `running`) could stop a double run."""
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = _job_in(admin, seed, case, "running")
    with role_connection(worker_dsn if actor == "app_worker" else admin_dsn) as conn:
        if actor == "app_worker":
            conn.execute("select set_config('app.job_case_id', %s, true)", (str(case),))
        code, rows, _ = attempt(conn, "update app.jobs set status = 'running' where id = %s", (job,))
    assert (code, rows) == (None, 1)


@pytest.mark.parametrize("status", [s for s in STATUSES if s != "queued"])
@pytest.mark.parametrize("actor", ["app_worker", "admin"])
def test_a_new_job_can_only_start_queued(
    status: str, actor: str, seed: Seed, worker_dsn: str, admin_dsn: str, admin: psycopg.Connection
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    with role_connection(worker_dsn if actor == "app_worker" else admin_dsn) as conn:
        if actor == "app_worker":
            conn.execute("select set_config('app.job_case_id', %s, true)", (str(case),))
        refused = attempt(conn, "insert into app.jobs (case_id, kind, status) values (%s, 'zz', %s)", (case, status))
        control = attempt(conn, "insert into app.jobs (case_id, kind) values (%s, 'zz')", (case,))
    assert refused[0] is not None, f"a job started {status}"
    assert control[0] is None, "control: a queued job may start"


# --- 11. one changing job per case ----------------------------------------------------------------------------------


async def test_two_case_changing_jobs_on_one_case_never_overlap_but_two_cases_run_together(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    experts = [seed.person() for _ in range(2)]
    cases = [seed.case(seed.org(kind="personal", members={e: "owner"}), e) for e in experts]
    spans: dict[uuid.UUID, tuple[float, float]] = {}
    registry = isolated_registry()

    @registry.kind("zz_changes", changes_case=True)
    async def changes(ctx: JobContext) -> None:
        start = time.monotonic()
        await asyncio.sleep(0.5)
        spans[ctx.job_id] = (start, time.monotonic())

    jobs: dict[uuid.UUID, list[uuid.UUID]] = {c: [] for c in cases}
    for expert, case in zip(experts, cases, strict=True):
        async with api_db.request(scope_for(expert)) as conn:
            for _ in range(2):
                jobs[case].append(await registry.enqueue(conn, case, "zz_changes"))
    everything = [j for js in jobs.values() for j in js]
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        done = await run_until_finished(worker, admin, everything, concurrency=4)
    assert set(done.values()) == {"done"}

    def overlap(a: uuid.UUID, b: uuid.UUID) -> bool:
        return spans[a][0] < spans[b][1] and spans[b][0] < spans[a][1]

    for case in cases:
        first, second = jobs[case]
        assert not overlap(first, second), "two jobs that change one case ran at the same time"
    assert any(overlap(a, b) for a in jobs[cases[0]] for b in jobs[cases[1]]), "control: other cases run in parallel"


# --- 12. the cancel flag ---------------------------------------------------------------------------------------------


async def test_a_cancel_after_the_job_finished_is_refused_and_leaves_no_flag_for_the_next_run(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    first = seed.job(case, requested_by=expert)
    async with api_db.request(scope_for(expert)) as conn:
        assert await repo.request_cancel(conn, case, expert) is True, "control: a live job can be cancelled"
        assert await repo.cancel_requested(conn, first) is True
    admin.execute("update app.jobs set status = 'running' where id = %s", (first,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (first,))  # completion beat any further cancel
    second = seed.job(case, requested_by=expert)
    async with api_db.request(scope_for(expert)) as conn:
        assert await repo.cancel_requested(conn, second) is False, "the old flag cancelled the next run"
        assert await repo.cancel_requested(conn, first) is True, "the flag still tells the first run's story"
    # a cancel that arrives when nothing is live does nothing and leaves no pre-cancelled state behind
    admin.execute("update app.jobs set status = 'running' where id = %s", (second,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (second,))
    before = admin.execute("select cancel_requested_at from app.case_processing where case_id = %s", (case,)).fetchone()
    async with api_db.request(scope_for(expert)) as conn:
        assert await repo.request_cancel(conn, case, expert) is False
    assert (
        admin.execute("select cancel_requested_at from app.case_processing where case_id = %s", (case,)).fetchone()
        == before
    )
    third = seed.job(case, requested_by=expert)
    async with api_db.request(scope_for(expert)) as conn:
        assert await repo.cancel_requested(conn, third) is False
