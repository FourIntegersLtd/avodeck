"""Two small additions the page-batch design leans on (ADR 0038).

- `busy_processing`: a waiter that finds the case busy pauses with this code. It is NOT resumable (it must keep no
  queue row: a sleeping locked row would block the parent's wake, F68), so the worker ends the queue row and leaves
  the job `paused` for the sweep to hand back.
- `ctx.progress(..., monotone=True)` never lowers `pct`; `job_id=` writes another job of the same case (a child
  reporting on its parent). Without them `progress` is unchanged.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import RESUMABLE_PAUSES, JobContext
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
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


def a_case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    return expert, seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)


def test_busy_processing_is_not_a_resumable_pause() -> None:
    assert "busy_processing" not in RESUMABLE_PAUSES


async def test_a_busy_processing_pause_ends_the_queue_row_and_leaves_the_job_paused(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case = a_case(seed)
    registry = isolated_registry()

    @registry.kind("zz_waiter", changes_case=True)
    async def waiter(ctx: JobContext) -> None:
        raise ProcessingPaused("busy_processing")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_waiter")
    async with open_worker(registry, worker_db, worker_dsn, pause_delays=(3600.0,)) as worker:
        assert (await run_until_finished(worker, admin, [job]))[job] == "paused"
    assert admin.execute("select error_code, attempts from app.jobs where id = %s", (job,)).fetchone() == (
        "busy_processing",
        1,
    )
    rows = admin.execute(
        "select status::text from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(job),)
    ).fetchall()
    assert rows == [("succeeded",)], "no sleeping `todo` row holding the case lock"


async def test_progress_is_unchanged_without_the_new_options(
    worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    _, case = a_case(seed)
    job = seed.job(case)
    admin.execute("update app.jobs set status = 'running', pct = 60 where id = %s", (job,))
    ctx = JobContext(worker_db, job, case, "process_bundle", 1)
    await ctx.progress(30)
    assert admin.execute("select pct from app.jobs where id = %s", (job,)).fetchone() == (30,)


async def test_monotone_progress_never_goes_back(worker_db: Database, admin: psycopg.Connection, seed: Seed) -> None:
    _, case = a_case(seed)
    job = seed.job(case)
    admin.execute("update app.jobs set status = 'running', pct = 60 where id = %s", (job,))
    ctx = JobContext(worker_db, job, case, "process_bundle", 1)
    await ctx.progress(30, monotone=True)
    assert admin.execute("select pct from app.jobs where id = %s", (job,)).fetchone() == (60,)
    await ctx.progress(80, monotone=True, step="sort", step_done=3, step_total=9)
    assert admin.execute("select pct, step, step_done, step_total from app.jobs where id = %s", (job,)).fetchone() == (
        80,
        "sort",
        3,
        9,
    )


async def test_a_child_can_report_on_another_job_of_its_case_only(
    worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    _, case = a_case(seed)
    _, other_case = a_case(seed)
    parent, child, stranger = seed.job(case), seed.job(case, kind="read_page_batch"), seed.job(other_case)
    for job in (parent, stranger):
        admin.execute("update app.jobs set status = 'running', pct = 10 where id = %s", (job,))
    ctx = JobContext(worker_db, child, case, "read_page_batch", 1)
    await ctx.progress(45, monotone=True, job_id=parent)
    await ctx.progress(45, monotone=True, job_id=stranger)  # the worker role sees its own case only
    pcts = {j: admin.execute("select pct from app.jobs where id = %s", (j,)).fetchone() for j in (parent, child, stranger)}
    assert pcts[parent] == (45,)
    assert pcts[stranger] == (10,), "another case's row is invisible to this job"
    assert pcts[child] != (45,), "its own row is left alone"
