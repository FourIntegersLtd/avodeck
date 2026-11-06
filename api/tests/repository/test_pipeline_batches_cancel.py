"""Stop after Try again, for a bundle read in page batches (ADR 0038, build step 8a).

`repo.cancel_requested` and `repo.request_cancel` compare the stored stop with `greatest(created_at, reopened_at)` of
the bundle job, `reopened_at` being the time its run was last reopened (Try again). So:

- a Stop made before the reopen does not stop the reopened children (the stored stop is older than the reopen);
- a second Stop after the reopen is written (`cancelled` is true) and stops the children again;
- a job with no run, and a run never reopened, behave exactly as before.

The children ask about the parent's id (`cancel_requested(parent)`), so the parent's own check and theirs are one rule.
Ids and booleans only (rule 6).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import psycopg
import pytest

from app.core.db import Database
from app.features.pipeline import repo
from tests.repository.test_pipeline_batches_support import add_batch, add_run, child_job, make_job
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@dataclass(frozen=True)
class World:
    expert: uuid.UUID
    case: uuid.UUID
    parent: uuid.UUID


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def world(admin: psycopg.Connection, seed: Seed) -> World:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert)
    parent = make_job(admin, seed, case, status="running")
    run = add_run(admin, case, parent, "ocr_waiting")
    add_batch(admin, run, case, 0, child=child_job(admin, seed, case, status="running"), pages=10)
    return World(expert, case, parent)


async def stop(api_db: Database, w: World) -> bool:
    async with api_db.request(scope_for(w.expert)) as conn:
        return await repo.request_cancel(conn, w.case, w.expert)


async def stopped(worker_db: Database, w: World) -> bool:
    async with worker_db.job(w.case) as conn:
        return await repo.cancel_requested(conn, w.parent)


def reopen(admin: psycopg.Connection, w: World) -> None:
    admin.execute("update app.pipeline_runs set reopened_at = now() where job_id = %s", (w.parent,))


async def test_a_stop_stops_the_run(api_db: Database, worker_db: Database, world: World) -> None:
    assert await stop(api_db, world)
    assert await stopped(worker_db, world)


async def test_a_stop_made_before_the_reopen_is_ignored(
    admin: psycopg.Connection, api_db: Database, worker_db: Database, world: World
) -> None:
    assert await stop(api_db, world)
    reopen(admin, world)
    assert not await stopped(worker_db, world)


async def test_stop_again_after_the_reopen_is_written_and_stops_the_children_again(
    admin: psycopg.Connection, api_db: Database, worker_db: Database, world: World
) -> None:
    assert await stop(api_db, world)
    reopen(admin, world)
    assert await stop(api_db, world)  # `cancelled = true`: the old stop does not hide this one
    assert await stopped(worker_db, world)


async def test_stop_twice_without_a_reopen_is_still_one_stop(
    api_db: Database, worker_db: Database, world: World
) -> None:
    assert await stop(api_db, world)
    assert not await stop(api_db, world)
    assert await stopped(worker_db, world)


async def test_a_job_with_no_run_is_stopped_as_before(
    admin: psycopg.Connection, api_db: Database, worker_db: Database, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert)
    parent = make_job(admin, seed, case, status="running")
    plain = World(expert, case, parent)
    assert await stop(api_db, plain)
    assert await stopped(worker_db, plain)
    assert not await stop(api_db, plain)


async def test_the_api_role_reads_no_run_row_to_decide_it(api_db: Database, world: World) -> None:
    """The stop is written by the API role, which has no access to `pipeline_runs`; the rule goes through a
    function that returns the reopen time and nothing else."""
    async with api_db.request(scope_for(world.expert)) as conn:
        row = await (await conn.execute("select app.pipeline_reopened_at(%s)", (world.parent,))).fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("select 1 from app.pipeline_runs")  # last: a refused statement aborts the transaction
    assert row == (None,)
