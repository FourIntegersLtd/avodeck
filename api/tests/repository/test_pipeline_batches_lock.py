"""Page-batch jobs take no case lock (ADR 0038, build step 11).

While a child holds the OCR open, the expert's own jobs of the case start and finish; during the tail, which holds the
case lock, a queued job waits for it and runs after. Counts, ids and codes only (rule 6).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.config import PipelineBatchSettings
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.storage import CaseFileStorage
from app.features.pipeline import service
from tests.repository.test_pipeline_batches_support import add_run, make_job
from tests.repository.test_pipeline_support import (
    Harness,
    expected,
    harness,
    recorded_book,
    rekeyed_store,
    subset_document,
    subset_page_bytes,
)
from tests.support.files import local_storage
from tests.support.jobs import open_worker
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ON = PipelineBatchSettings(pages=5, window=2, min_pages=1)
PROBE = "probe_case_job"


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def recordings(tmp_path: Path) -> FixtureStore:
    return rekeyed_store(tmp_path / "recordings", subset_document(), subset_page_bytes())


@pytest.fixture
async def h(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    import datetime as dt

    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    case = expected("case.json")
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        ocr_store=recordings,
        bedrock=recorded_book(recordings),
        names=list(case["case_names"]),
        dates_of_birth=[dt.date.fromisoformat(case["dob"])],
        batches=ON,
    ) as harnessed:
        ran: list[uuid.UUID] = []

        async def probe(ctx: JobContext) -> None:
            ran.append(ctx.job_id)

        harnessed.registry.kind(PROBE, changes_case=True)(probe)
        harnessed.ran = ran  # type: ignore[attr-defined]
        yield harnessed


def status_of(h: Harness, job: uuid.UUID) -> str:
    row = h.admin.execute("select status from app.jobs where id = %s", (job,)).fetchone()
    assert row is not None
    return str(row[0])


async def until(check: Callable[[], bool], what: str, *, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.05)


@contextlib.asynccontextmanager
async def running(h: Harness, *, concurrency: int = 3) -> AsyncIterator[None]:
    async with open_worker(h.registry, h.worker_db, h.worker_dsn) as worker:
        task = asyncio.create_task(worker.run(concurrency=concurrency))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def gate_ocr(h: Harness) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold every OCR call of the case open until the returned `release` is set; `entered` is set by the first one."""
    assert h.ocr is not None
    entered, release = asyncio.Event(), asyncio.Event()
    inner = h.ocr.inner
    real = inner.analyse

    async def held(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await real(*args, **kwargs)

    inner.analyse = held  # type: ignore[method-assign]
    return entered, release


def gate_tail(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold the parent's tail (it runs under the case lock) at its start."""
    entered, release = asyncio.Event(), asyncio.Event()
    real: Callable[..., Awaitable[Any]] = service._tail

    async def held(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(service, "_tail", held)
    return entered, release


async def test_a_case_job_starts_and_finishes_while_a_child_holds_the_ocr_open(h: Harness) -> None:
    entered, release = gate_ocr(h)
    await h.upload(subset_document())
    async with running(h):
        await asyncio.wait_for(entered.wait(), 60)
        probe = await h.enqueue(PROBE)
        await until(lambda: status_of(h, probe) == "done", "the case job to finish while the OCR is held open")
        children = h.admin.execute(
            "select status from app.jobs where case_id = %s and kind = 'read_page_batch'", (h.case_id,)
        ).fetchall()
        assert children and all(c[0] != "done" for c in children), "a child finished though the OCR was held"
        release.set()
        await until(
            lambda: all(status_of(h, j) == "done" for j in h.job_ids()),
            "the bundle to finish",
            timeout=300,
        )


async def test_a_job_queued_during_the_tail_waits_for_the_case_lock_and_runs_after(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = gate_tail(monkeypatch)
    await h.upload(subset_document())
    async with running(h):
        await asyncio.wait_for(entered.wait(), 300)
        parent = h.admin.execute(
            "select id from app.jobs where case_id = %s and kind in ('process_bundle', 'process_pages')"
            " order by created_at limit 1",
            (h.case_id,),
        ).fetchone()
        assert parent is not None
        probe = await h.enqueue(PROBE)
        await asyncio.sleep(1.0)
        assert status_of(h, probe) != "done", "a case job ran while the tail held the case lock"
        assert status_of(h, parent[0]) == "running"
        release.set()
        await until(lambda: status_of(h, probe) == "done", "the case job to run after the tail", timeout=120)
        assert status_of(h, parent[0]) == "done", "the case job finished before the tail"


def parent_of(h: Harness, *, not_this: uuid.UUID) -> uuid.UUID:
    row = h.admin.execute(
        "select id from app.jobs where case_id = %s and kind in ('process_bundle', 'process_pages') and id <> %s"
        " order by created_at limit 1",
        (h.case_id, not_this),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def lock_waiters(h: Harness) -> int:
    """Backends of this database waiting for a lock (a count only)."""
    row = h.admin.execute(
        "select count(*) from pg_stat_activity where datname = current_database() and wait_event_type = 'Lock'"
    ).fetchone()
    assert row is not None
    return int(row[0])


async def test_planning_the_sort_takes_every_run_lock_before_it_changes_its_own_run(
    h: Harness, admin_dsn: str, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F62: the parent's wake A used to change its own run row first and wait for the other runs' rows after. A child
    of an older run that completes meanwhile takes the older row, then waits for this one: a deadlock. The parent
    must hold nothing while it waits for the case's run rows, so the holder of an older row can still take its row."""
    older = make_job(h.admin, seed, h.case_id, status="done")
    add_run(h.admin, h.case_id, older, "done")
    holder = psycopg.connect(admin_dsn, autocommit=False)
    taken = asyncio.Event()
    real = service._name_language

    async def hold_the_older_run(*args: Any, **kwargs: Any) -> Any:
        await asyncio.to_thread(
            holder.execute, "select 1 from app.pipeline_runs where job_id = %s for update", (older,)
        )
        taken.set()
        return await real(*args, **kwargs)

    monkeypatch.setattr(service, "_name_language", hold_the_older_run)
    try:
        await h.upload(subset_document())
        async with running(h):
            await asyncio.wait_for(taken.wait(), 300)
            waiting_before = lock_waiters(h)
            await until(lambda: lock_waiters(h) > waiting_before, "the parent to wait for the older run's row")
            parent = parent_of(h, not_this=older)
            try:
                await asyncio.to_thread(
                    holder.execute, "select 1 from app.pipeline_runs where job_id = %s for update nowait", (parent,)
                )
            except psycopg.errors.LockNotAvailable:
                pytest.fail("the parent holds its own run row while it waits for an older run's row")
            finally:
                await asyncio.to_thread(holder.rollback)
            await until(
                lambda: all(status_of(h, j) == "done" for j in h.job_ids() if j != older),
                "the bundle to finish",
                timeout=300,
            )
    finally:
        holder.close()


async def test_children_that_finish_before_the_parent_parks_do_not_need_another_wake(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F57: a child that completes while the parent's own queue row is still held cannot wake it (a wake is refused
    while the row is held). The parent looks again before it parks, so the tail runs in the same wake and nothing
    waits for the heal sweep."""
    real_plan = service._plan_sort
    real_mode = service._batch_mode
    exits: list[str] = []

    def finish_the_sort_batches(job: uuid.UUID) -> None:
        h.admin.execute("select set_config('app.job_case_id', %s, false)", (str(h.case_id),))
        try:
            for (batch,) in h.admin.execute(
                "select id from app.pipeline_batches where run_job_id = %s and phase = 'sort' order by seq", (job,)
            ).fetchall():
                h.admin.execute("select app.pipeline_complete_batch(%s)", (batch,))
        finally:
            h.admin.execute("reset app.job_case_id")

    async def children_finish_first(ctx: JobContext, services: Any) -> bool:
        planned: bool = await real_plan(ctx, services)
        if planned:
            finish_the_sort_batches(ctx.job_id)
        return planned

    async def watched(ctx: JobContext, services: Any) -> None:
        try:
            await real_mode(ctx, services)
        finally:
            row = h.admin.execute("select state from app.pipeline_runs where job_id = %s", (ctx.job_id,)).fetchone()
            exits.append(str(row[0]) if row else "gone")

    monkeypatch.setattr(service, "_plan_sort", children_finish_first)
    monkeypatch.setattr(service, "_batch_mode", watched)
    await h.upload(subset_document())
    async with running(h):
        await until(lambda: len(exits) >= 2, "the parent's second wake to end", timeout=300)
    assert exits[1] == "done", f"the second wake ended with the run {exits[1]!r}: nothing woke the parent"
