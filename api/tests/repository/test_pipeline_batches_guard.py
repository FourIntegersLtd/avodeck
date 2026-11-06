"""The write guard for page-batch children (ADR 0038, F64; build step 7).

`repo.batch_write_allowed(conn, batch_id, page_id)` is the first statement of every child page write. It takes the
batch's run row `for share`, then the page row `for share`, and says False (the child ends quietly, writing
nothing) when the run is missing or closed, or the page is a confirmed or deleted quarantine, superseded, or gone.
Quarantine confirm locks the same page row `for update` before it changes anything, so a confirm and a child write
on one page take turns. Ids and numbers only (rule 6).
"""

from __future__ import annotations

import asyncio
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
    run: uuid.UUID
    batch: uuid.UUID
    pages: list[uuid.UUID]


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def world(admin: psycopg.Connection, seed: Seed) -> World:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert)
    document = seed.document(case)
    pages = [seed.page(case, document, n) for n in (1, 2, 3)]
    parent = make_job(admin, seed, case)
    run = add_run(admin, case, parent, "ocr_waiting")
    batch = add_batch(admin, run, case, 0, child=child_job(admin, seed, case, status="running"), pages=3)
    return World(expert, case, run, batch, pages)


async def allowed(db: Database, w: World, page: uuid.UUID, batch: uuid.UUID | None = None) -> bool:
    async with db.job(w.case) as conn:
        return await repo.batch_write_allowed(conn, batch or w.batch, page)


def quarantine_row(admin: psycopg.Connection, w: World, page: uuid.UUID, status: str) -> uuid.UUID:
    row = admin.execute(
        "insert into app.page_quarantines (case_id, bundle_id, page_id, reason, status)"
        " values (%s, (select bundle_id from app.cases where id = %s), %s, 'zz_reason', %s) returning id",
        (w.case, w.case, page, status),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


async def test_a_page_of_an_open_run_may_be_written(worker_db: Database, world: World) -> None:
    assert await allowed(worker_db, world, world.pages[0])


@pytest.mark.parametrize("state", ["ocr_ready", "sort_waiting", "finishing", "done"])
async def test_a_run_that_is_not_closed_allows_the_write(
    admin: psycopg.Connection, worker_db: Database, world: World, state: str
) -> None:
    admin.execute("update app.pipeline_runs set state = %s where job_id = %s", (state, world.run))
    assert await allowed(worker_db, world, world.pages[0])


async def test_a_closed_run_refuses_the_write(admin: psycopg.Connection, worker_db: Database, world: World) -> None:
    admin.execute("update app.pipeline_runs set state = 'closed', closed_at = now() where job_id = %s", (world.run,))
    assert not await allowed(worker_db, world, world.pages[0])


async def test_a_missing_batch_refuses_the_write(worker_db: Database, world: World) -> None:
    assert not await allowed(worker_db, world, world.pages[0], batch=uuid.uuid4())


async def test_a_missing_page_refuses_the_write(worker_db: Database, world: World) -> None:
    assert not await allowed(worker_db, world, uuid.uuid4())


async def test_a_page_set_aside_as_quarantined_refuses_the_write(
    admin: psycopg.Connection, worker_db: Database, world: World
) -> None:
    admin.execute("update app.pages set quarantined = true where id = %s", (world.pages[1],))
    assert not await allowed(worker_db, world, world.pages[1])
    assert await allowed(worker_db, world, world.pages[0])


@pytest.mark.parametrize("status", ["confirmed", "purged"])
async def test_a_confirmed_or_deleted_quarantine_refuses_the_write(
    admin: psycopg.Connection, worker_db: Database, world: World, status: str
) -> None:
    quarantine_row(admin, world, world.pages[1], status)
    assert not await allowed(worker_db, world, world.pages[1])


@pytest.mark.parametrize("status", ["suggested", "dismissed", "undone"])
async def test_a_quarantine_that_is_not_confirmed_allows_the_write(
    admin: psycopg.Connection, worker_db: Database, world: World, status: str
) -> None:
    quarantine_row(admin, world, world.pages[1], status)
    assert await allowed(worker_db, world, world.pages[1])


async def test_a_superseded_page_refuses_the_write(
    admin: psycopg.Connection, worker_db: Database, world: World
) -> None:
    admin.execute("update app.pages set superseded_by_page_id = %s where id = %s", (world.pages[2], world.pages[1]))
    assert not await allowed(worker_db, world, world.pages[1])


async def test_the_guard_holds_the_run_row_and_the_page_row_for_share(
    admin_dsn: str, worker_db: Database, world: World
) -> None:
    """A purge (run rows `for update`) and a quarantine confirm (page row `for update`) wait for a child that is
    inside its write; a plain reader does not."""
    async with worker_db.job(world.case) as conn:
        assert await repo.batch_write_allowed(conn, world.batch, world.pages[0])
        with psycopg.connect(admin_dsn, autocommit=True) as probe:
            run_free = probe.execute(
                "select 1 from app.pipeline_runs where job_id = %s for update skip locked", (world.run,)
            ).fetchone()
            page_free = probe.execute(
                "select 1 from app.pages where id = %s for update skip locked", (world.pages[0],)
            ).fetchone()
            other_page = probe.execute(
                "select 1 from app.pages where id = %s for update skip locked", (world.pages[1],)
            ).fetchone()
            reader = probe.execute("select 1 from app.pages where id = %s", (world.pages[0],)).fetchone()
    assert run_free is None
    assert page_free is None
    assert other_page is not None
    assert reader is not None


# --- quarantine confirm racing a child write (two connections, barrier) ----------------------------------------


def suggested_quarantine(admin: psycopg.Connection, w: World, page: uuid.UUID) -> repo.Quarantine:
    qid = quarantine_row(admin, w, page, "suggested")
    return repo.Quarantine(qid, page, 1, "suggested", "zz_reason")


def a_suggestion(admin: psycopg.Connection, w: World, page: uuid.UUID) -> list[repo.NewFact]:
    admin.execute(
        "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, handwritten, struck,"
        " source) select %s, bundle_id, 1, 'w1', '\\x00'::bytea, '{0,0,1,0,1,1,0,1}', 0.9, false, false, 'ocr'"
        " from app.pages where id = %s",
        (page, page),
    )
    return [repo.NewFact(kind="zz_kind", fields={}, value="v", word_ids=["w1"], bbox={})]


async def test_a_confirm_that_is_running_makes_the_child_wait_and_then_refuse(
    admin: psycopg.Connection, api_db: Database, worker_db: Database, world: World
) -> None:
    page = world.pages[0]
    q = suggested_quarantine(admin, world, page)
    release = asyncio.Event()
    holding = asyncio.Event()

    async def confirm() -> None:
        async with api_db.request(scope_for(world.expert)) as conn:
            await repo.confirm_quarantine(conn, q, "zz_reason", world.expert)
            holding.set()
            await release.wait()

    async def child() -> bool:
        return await allowed(worker_db, world, page)

    confirming = asyncio.create_task(confirm())
    await asyncio.wait({confirming, asyncio.create_task(holding.wait())}, return_when=asyncio.FIRST_COMPLETED)
    assert holding.is_set(), "the confirm stopped before it held the page row"
    writing = asyncio.create_task(child())
    done, _ = await asyncio.wait({writing}, timeout=0.7)
    assert not done, "the child went on while a confirm held the page row"
    release.set()
    await confirming
    assert await writing is False


async def test_a_child_that_holds_the_lock_first_finishes_before_the_confirm_proceeds(
    admin: psycopg.Connection, api_db: Database, worker_db: Database, world: World
) -> None:
    """The suggestion the child writes is on the page when the confirm runs, so the confirm rejects it: no
    suggestion is left on the quarantined page."""
    page = world.pages[0]
    q = suggested_quarantine(admin, world, page)
    facts = a_suggestion(admin, world, page)
    inside = asyncio.Event()
    go_on = asyncio.Event()

    async def child() -> None:
        async with worker_db.job(world.case) as conn:
            assert await repo.batch_write_allowed(conn, world.batch, page)
            inside.set()
            await go_on.wait()
            await repo.store_extraction(
                conn, world.case, page, source_type=None, facts=facts, sealed=None, needs_human=False, reason=None,
                task="zz", model_id="zz", prompt_version="zz", entries=[],
            )  # fmt: skip

    async def confirm() -> None:
        async with api_db.request(scope_for(world.expert)) as conn:
            await repo.confirm_quarantine(conn, q, "zz_reason", world.expert)

    writing = asyncio.create_task(child())
    await asyncio.wait({writing, asyncio.create_task(inside.wait())}, return_when=asyncio.FIRST_COMPLETED)
    assert inside.is_set(), "the child stopped before it was inside its write"
    confirming = asyncio.create_task(confirm())
    done, _ = await asyncio.wait({confirming}, timeout=0.7)
    assert not done, "the confirm went on while a child held the page row"
    go_on.set()
    await writing
    await confirming
    left = admin.execute(
        "select count(*) from app.facts where page_id = %s and status <> 'rejected'", (page,)
    ).fetchone()
    assert left == (0,)
    total = admin.execute("select count(*) from app.facts where page_id = %s", (page,)).fetchone()
    assert total == (1,)


async def test_a_page_outside_the_batchs_range_is_refused_and_the_edges_are_allowed(
    admin: psycopg.Connection, worker_db: Database, world: World
) -> None:
    admin.execute("update app.pipeline_batches set first_page_no = 2, last_page_no = 2 where id = %s", (world.batch,))
    assert not await allowed(worker_db, world, world.pages[0]), "page 1 is just before the batch's range"
    assert await allowed(worker_db, world, world.pages[1])
    assert not await allowed(worker_db, world, world.pages[2]), "page 3 is just after the batch's range"
    admin.execute("update app.pipeline_batches set first_page_no = 1, last_page_no = 3 where id = %s", (world.batch,))
    assert await allowed(worker_db, world, world.pages[0])
    assert await allowed(worker_db, world, world.pages[2])
