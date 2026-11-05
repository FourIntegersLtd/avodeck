"""A page-batch job holds at most `cap` pool checkouts at once (ADR 0038, "Bounds on concurrency").

A handler calls `ctx.limit_checkouts(cap)`. From then on every `Database.job()` checkout made by that job's tasks
(its own transactions, `ctx.progress`, `ctx.step`, and the AI kit's, which opens `database.job` itself) takes one of
`cap` permits while it is held. The worker's own checkouts (status writes, sweeps) are never capped, and the limit
ends with the handler call, so a capped handler that raises still gets its status written when the pool is at the cap.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext, checkout_cap
from tests.support.checkouts import CheckoutSpy
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=10)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def a_case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    return expert, seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)


async def hold_briefly(ctx: JobContext) -> None:
    async with ctx.transaction() as conn:
        await conn.execute("select 1")
        await asyncio.sleep(0.05)


def test_the_cap_is_the_pool_less_its_reserve_shared_between_the_jobs() -> None:
    assert checkout_cap(pool_max=10, concurrency=4) == 2
    assert checkout_cap(pool_max=10, concurrency=1) == 8
    assert checkout_cap(pool_max=3, concurrency=4) == 1, "never below one"


async def test_a_capped_job_never_holds_more_than_its_cap(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    expert, case = a_case(seed)
    spy = CheckoutSpy(worker_db).install(monkeypatch)
    registry = isolated_registry()

    @registry.kind("zz_capped", changes_case=False)
    async def capped(ctx: JobContext) -> None:
        ctx.limit_checkouts(2)

        async def progress() -> None:
            await ctx.progress(10)

        async def step(n: int) -> None:
            await ctx.step(f"s{n}", _quick)

        await asyncio.gather(*[hold_briefly(ctx) for _ in range(6)], progress(), progress(), step(1), step(2))

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_capped")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert (await run_until_finished(worker, admin, [job]))[job] == "done"
    assert spy.total >= 10
    assert spy.peak == 2, "six tasks wanted a connection at once; two got one"
    assert spy.nested == 0, "no task took a second checkout while it held one"


async def _quick(conn: psycopg.AsyncConnection) -> None:
    await conn.execute("select 1")


async def test_a_job_that_did_not_ask_is_not_capped(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    expert, case = a_case(seed)
    spy = CheckoutSpy(worker_db).install(monkeypatch)
    uncapped_peak = 0
    registry = isolated_registry()

    @registry.kind("zz_plain", changes_case=False)
    async def plain(ctx: JobContext) -> None:
        nonlocal uncapped_peak
        in_flight = 0

        async def one() -> None:
            nonlocal in_flight, uncapped_peak
            async with ctx.transaction() as conn:
                in_flight += 1
                uncapped_peak = max(uncapped_peak, in_flight)
                await conn.execute("select 1")
                await asyncio.sleep(0.5)  # long enough for the pool to open a sixth connection
                in_flight -= 1

        await asyncio.gather(*[one() for _ in range(6)])

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_plain")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert (await run_until_finished(worker, admin, [job]))[job] == "done"
    assert uncapped_peak == 6 and spy.total == 0, "other kinds keep today's behaviour"


async def test_four_capped_jobs_on_a_pool_of_ten_hold_at_most_eight(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    spy = CheckoutSpy(worker_db).install(monkeypatch)
    registry = isolated_registry()

    @registry.kind("zz_batch_like", changes_case=False)
    async def batch_like(ctx: JobContext) -> None:
        ctx.limit_checkouts(checkout_cap(pool_max=worker_db.max_size, concurrency=4))
        await asyncio.gather(*[hold_briefly(ctx) for _ in range(6)])

    jobs = []
    for _ in range(4):
        expert, case = a_case(seed)
        async with api_db.request(scope_for(expert)) as conn:
            jobs.append(await registry.enqueue(conn, case, "zz_batch_like"))
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        found = await run_until_finished(worker, admin, jobs, concurrency=4)
    assert set(found.values()) == {"done"}
    assert 3 <= spy.peak <= 8, "the jobs overlapped, and together stayed within the usable pool"
    assert spy.nested == 0


async def test_a_capped_handler_that_pauses_still_gets_its_status_written(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed,
) -> None:  # fmt: skip
    """The limit ends with the handler call, before the worker writes how the job ended: a handler holding all of
    its permits when it raises must not leave the worker waiting for one."""
    expert, case = a_case(seed)
    registry = isolated_registry()
    held: list[object] = []

    @registry.kind("zz_pauses_at_cap", changes_case=False)
    async def pauses(ctx: JobContext) -> None:
        ctx.limit_checkouts(1)
        manager = ctx.transaction()
        await manager.__aenter__()  # holds the only permit when it raises
        held.append(manager)
        raise ProcessingPaused("cost_guard")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case, "zz_pauses_at_cap")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        found = await run_until_finished(worker, admin, [job], timeout=15)
    assert found[job] == "paused"
    assert admin.execute("select error_code from app.jobs where id = %s", (job,)).fetchone() == ("cost_guard",)
    for manager in held:
        await manager.__aexit__(None, None, None)  # type: ignore[attr-defined]


async def test_the_limit_does_not_leak_into_the_next_job_on_the_same_worker(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    expert, case = a_case(seed)
    spy = CheckoutSpy(worker_db).install(monkeypatch)
    registry = isolated_registry()

    @registry.kind("zz_first_capped", changes_case=False)
    async def first(ctx: JobContext) -> None:
        ctx.limit_checkouts(1)

    @registry.kind("zz_then_plain", changes_case=False)
    async def second(ctx: JobContext) -> None:
        await hold_briefly(ctx)

    async with api_db.request(scope_for(expert)) as conn:
        jobs = [
            await registry.enqueue(conn, case, "zz_first_capped"),
            await registry.enqueue(conn, case, "zz_then_plain"),
        ]
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert set((await run_until_finished(worker, admin, jobs)).values()) == {"done"}
    assert spy.total == 0


async def test_a_cap_below_one_is_refused(api_db: Database, worker_db: Database) -> None:
    ctx = JobContext(worker_db, uuid.uuid4(), uuid.uuid4(), "zz", 1)
    with pytest.raises(ValueError):
        ctx.limit_checkouts(0)
