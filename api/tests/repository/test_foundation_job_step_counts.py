"""Jobs count their steps (migration 0028): `jobs.step_done` of `jobs.step_total`, for "page 40 of 900".

`ctx.progress()` stores the numbers with the percentage; they are whole numbers, never text (rule 6).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.jobs import JobContext
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
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


def test_a_new_job_has_no_counts(seed: Seed, admin: psycopg.Connection) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = seed.job(case)
    assert admin.execute("select step_done, step_total from app.jobs where id = %s", (job,)).fetchone() == (None, None)


def test_the_counts_are_whole_numbers_that_make_sense(seed: Seed, admin: psycopg.Connection) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = seed.job(case)
    admin.execute("update app.jobs set step_done = 3, step_total = 10 where id = %s", (job,))
    for done, total in ((-1, 10), (11, 10), (1, 0)):
        with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
            admin.execute("update app.jobs set step_done = %s, step_total = %s where id = %s", (done, total, job))


async def test_progress_stores_the_counts_and_keeps_them_when_a_later_call_leaves_them_out(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    registry = isolated_registry()
    seen: list[object] = []

    @registry.kind("zz_counts")
    async def counts(ctx: JobContext) -> None:
        await ctx.progress(40, eta_seconds=12, step="ocr", step_done=360, step_total=900)
        seen.append(admin.execute("select step_done, step_total from app.jobs where id = %s", (ctx.job_id,)).fetchone())
        await ctx.progress(45)
        seen.append(admin.execute("select step_done, step_total from app.jobs where id = %s", (ctx.job_id,)).fetchone())
        await ctx.progress(50, step="split")  # a new step: the counts start again
        seen.append(admin.execute("select step_done, step_total from app.jobs where id = %s", (ctx.job_id,)).fetchone())

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_counts")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_finished(worker, admin, [job])
    assert seen == [(360, 900), (360, 900), (None, None)]
