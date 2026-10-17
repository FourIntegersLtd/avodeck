"""`jobs.requested_by`: the person who queued a job (architecture 4.1; request from slice H).

`app.enqueue_job()` records the signed-in person; a job queued by another job (or the daily run)
has none. Nobody can set it by hand: the database takes it from the request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.jobs import JobContext
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


async def test_a_job_records_who_queued_it(
    api_db: Database, worker_db: Database, seed: Seed, admin: psycopg.Connection
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert)
    registry = isolated_registry()

    @registry.kind("zz_work")
    async def work(ctx: JobContext) -> None:
        return None

    async with api_db.request(scope_for(expert)) as conn:
        by_person = await registry.enqueue(conn, case, "zz_work")
    async with worker_db.job(case) as conn:
        by_job = await registry.enqueue(conn, case, "zz_work")
    rows = dict(admin.execute("select id, requested_by from app.jobs where id = any(%s)", ([by_person, by_job],)))
    assert rows == {by_person: expert, by_job: None}


def test_nobody_sets_the_requester_by_hand(
    api_dsn: str, worker_dsn: str, seed: Seed, admin: psycopg.Connection
) -> None:
    expert, other = seed.person(), seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert)
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(expert),))
        row = conn.execute(
            "insert into app.jobs (case_id, kind, requested_by) values (%s, 'process_bundle', %s) returning id",
            (case, other),
        ).fetchone()
    assert row is not None
    job = row[0]
    with psycopg.connect(worker_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.job_case_id', %s, true)", (str(case),))
        conn.execute("update app.jobs set requested_by = %s where id = %s", (other, job))
    assert admin.execute("select requested_by from app.jobs where id = %s", (job,)).fetchone() == (expert,)
