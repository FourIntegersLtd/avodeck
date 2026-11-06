"""Jobs that used the case lock to wait for the bundle now wait without a queue row (ADR 0038, build step 9).

`retry_unread_pages`, `reprocess_case`, `extract_entries` and `repair_fact_dates` begin with one check,
`app.pipeline_case_busy`. While a page-batch run is busy (its parent running or queued, paused with a resumable code, or
a child with a queue row, sleeping ones included) they raise `ProcessingPaused("busy_processing")`: the worker ends the
queue row and leaves the job `paused`, because that code is not resumable. A run left open by a Stop, a failure or a
cost-guard pause is not busy, and neither is a case with no run at all (flag off: nothing changes). The sweep hands the
waiter back once the case is no longer busy, and it then runs once. Ids and codes only (rule 6).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import psycopg
import pytest

from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import RESUMABLE_PAUSES, JobContext
from app.core.storage import CaseFileStorage
from app.features.pipeline import service
from tests.repository.test_pipeline_batches_support import (
    add_batch,
    add_row,
    add_run,
    child_job,
    job_state,
    make_job,
    rows_of,
)
from tests.repository.test_pipeline_support import Harness, harness
from tests.support.files import local_storage
from tests.support.jobs import open_worker, run_until_finished
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

Waiter = Callable[[JobContext], Awaitable[None]]
WAITERS: dict[str, Waiter] = {
    service.RETRY_UNREAD_PAGES: service.retry_unread_pages,
    service.REPROCESS_CASE: service.reprocess_case,
    service.EXTRACT_ENTRIES: service.extract_entries,
    service.REPAIR_FACT_DATES: service.repair_fact_dates,
}
KINDS = sorted(WAITERS)
RESUMABLE = sorted(RESUMABLE_PAUSES)


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def h(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
    ) as running:
        yield running


def waiter_ctx(h: Harness, seed: Seed, kind: str) -> JobContext:
    return JobContext(h.worker_db, seed.job(h.case_id, kind=kind), h.case_id, kind, 1)


async def pauses_busy(h: Harness, seed: Seed, kind: str) -> bool:
    try:
        await WAITERS[kind](waiter_ctx(h, seed, kind))
    except ProcessingPaused as paused:
        assert paused.reason == "busy_processing"
        return True
    return False


def open_run(h: Harness, seed: Seed, *, parent: str = "running", code: str | None = None) -> uuid.UUID:
    job = make_job(h.admin, seed, h.case_id, status=parent, code=code)
    return add_run(h.admin, h.case_id, job, "ocr_waiting")


# --- the check, by itself ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
async def test_a_waiter_pauses_while_the_runs_parent_is_running(h: Harness, seed: Seed, kind: str) -> None:
    open_run(h, seed)
    assert await pauses_busy(h, seed, kind)


@pytest.mark.parametrize("kind", KINDS)
async def test_a_waiter_pauses_while_a_child_waits_in_the_queue(h: Harness, seed: Seed, kind: str) -> None:
    """The parent is parked paused `service_busy` (mirrored from a sleeping child): the children are still busy."""
    run = open_run(h, seed, parent="paused", code="service_busy")
    child = child_job(h.admin, seed, h.case_id, status="paused", code="service_busy")
    add_batch(h.admin, run, h.case_id, 0, child=child, pages=10)
    add_row(h.admin, child, h.case_id, lock=None, delay_seconds=600)
    assert await pauses_busy(h, seed, kind)


@pytest.mark.parametrize("code", RESUMABLE)
async def test_a_waiter_pauses_while_the_parent_is_paused_with_a_resumable_code(
    h: Harness, seed: Seed, code: str
) -> None:
    open_run(h, seed, parent="paused", code=code)
    assert await pauses_busy(h, seed, service.RETRY_UNREAD_PAGES)


@pytest.mark.parametrize("kind", KINDS)
async def test_a_waiter_runs_after_a_stop(h: Harness, seed: Seed, kind: str) -> None:
    """A run left open by a Stop (parent paused `cancelled`, no queue row) does not strand the waiter."""
    open_run(h, seed, parent="paused", code="cancelled")
    assert not await pauses_busy(h, seed, kind)


@pytest.mark.parametrize("kind", KINDS)
async def test_a_waiter_runs_after_a_failure(h: Harness, seed: Seed, kind: str) -> None:
    open_run(h, seed, parent="failed")
    assert not await pauses_busy(h, seed, kind)


@pytest.mark.parametrize("kind", KINDS)
async def test_a_waiter_runs_when_the_run_has_ended(h: Harness, seed: Seed, kind: str) -> None:
    run = open_run(h, seed, parent="done")
    h.admin.execute("update app.pipeline_runs set state = 'done', finished_at = now() where job_id = %s", (run,))
    assert not await pauses_busy(h, seed, kind)


@pytest.mark.parametrize("kind", KINDS)
async def test_a_waiter_runs_at_once_when_the_case_has_no_run(h: Harness, seed: Seed, kind: str) -> None:
    """Flag off: no run exists, nothing is ever busy, the four kinds behave as they did."""
    assert not await pauses_busy(h, seed, kind)


# --- through the worker and the sweep --------------------------------------------------------------------------------


async def test_a_waiter_ends_paused_with_no_queue_row_and_runs_once_when_handed_back(h: Harness, seed: Seed) -> None:
    open_run(h, seed)
    job = await h.enqueue(service.RETRY_UNREAD_PAGES)
    async with open_worker(h.registry, h.worker_db, h.worker_dsn) as worker:
        found = await run_until_finished(worker, h.admin, [job])
    assert found[job] == "paused"
    state = job_state(h.admin, job)
    assert state[1] == "busy_processing"
    assert [r for r in rows_of(h.admin, job) if r[1] in ("todo", "doing")] == []

    # While the run is busy the sweep leaves it alone.
    h.admin.execute("select app.pipeline_heal_runs(%s)", (RESUMABLE,))  # its effect is asserted below, not its count
    assert job_state(h.admin, job)[0] == "paused"
    assert [r for r in rows_of(h.admin, job) if r[1] in ("todo", "doing")] == []

    # The expert stops the run: the parent is paused `cancelled`, no longer busy, and the sweep hands the waiter back.
    parent = h.admin.execute("select job_id from app.pipeline_runs where case_id = %s", (h.case_id,)).fetchone()
    assert parent is not None
    h.admin.execute("update app.jobs set status = 'paused', error_code = 'cancelled' where id = %s", (parent[0],))
    # The sweep above re-queued the parent (its run was waiting with no queue row); the real worker consumes that row
    # when it ends the stopped parent. Take it away here, or it would hold the case lock ahead of the waiter's row.
    h.admin.execute("set search_path to procrastinate, public")  # its status trigger names unqualified types
    h.admin.execute(
        "update procrastinate_jobs set status = 'succeeded' where status = 'todo' and args ->> 'job_id' = %s",
        (str(parent[0]),),
    )
    h.admin.execute("reset search_path")
    h.admin.execute("select app.pipeline_heal_runs(%s)", (RESUMABLE,))
    assert len([r for r in rows_of(h.admin, job) if r[1] == "todo"]) == 1

    # `run_until_finished` would return at once here (the job is still `paused` until the worker takes the new row),
    # so the wait is for `done` itself.
    async with open_worker(h.registry, h.worker_db, h.worker_dsn) as worker:
        task = asyncio.create_task(worker.run(concurrency=1))
        try:
            for _ in range(600):
                if job_state(h.admin, job)[0] == "done" or task.done():
                    break
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    assert job_state(h.admin, job)[0] == "done"
    h.admin.execute("select app.pipeline_heal_runs(%s)", (RESUMABLE,))
    assert [r for r in rows_of(h.admin, job) if r[1] == "todo"] == []
