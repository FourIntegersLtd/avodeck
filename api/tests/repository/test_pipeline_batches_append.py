"""A second bundle job while a healthy run is open (ADR 0038, build step 11; the append case).

A start (or an append) that finds an older run whose parent is healthy leaves that run open, plans only the pages
outside its range, and its own tail waits for the older run's. Counts, ids and codes only (rule 6).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import time
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.config import PipelineBatchSettings
from app.core.db import Database
from app.core.storage import CaseFileStorage
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
PAGES = 18


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
        yield harnessed


async def until(check: Callable[[], bool], what: str, *, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.05)


def state_of(h: Harness, job: uuid.UUID) -> tuple[Any, ...] | None:
    return h.admin.execute(
        "select r.state, j.status from app.pipeline_runs r join app.jobs j on j.id = r.job_id where r.job_id = %s",
        (job,),
    ).fetchone()


async def test_a_start_while_a_healthy_run_is_open_leaves_it_open_and_waits_for_its_tail(h: Harness) -> None:
    assert h.ocr is not None
    real = h.ocr.inner.analyse
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await real(*args, **kwargs)

    h.ocr.inner.analyse = held  # type: ignore[method-assign]
    await h.upload(subset_document())
    async with open_worker(h.registry, h.worker_db, h.worker_dsn) as worker:
        task = asyncio.create_task(worker.run(concurrency=3))
        try:
            await asyncio.wait_for(entered.wait(), 120)
            old = h.admin.execute("select job_id from app.pipeline_runs where case_id = %s", (h.case_id,)).fetchone()
            assert old is not None
            started = await h.client.post(f"/v1/cases/{h.case_id}/process")
            assert started.status_code == 202
            new = uuid.UUID(started.json()["job_id"])
            await until(lambda: state_of(h, new) is not None, "the newer run to be planned")
            old_state = state_of(h, old[0])
            assert old_state is not None and old_state[0] != "closed", "the healthy run was closed"
            batches = h.admin.execute("select count(*) from app.pipeline_batches where run_job_id = %s", (new,))
            assert batches.fetchone() == (0,), "the newer run planned pages that are inside the older run's range"
            release.set()
            await until(
                lambda: state_of(h, new) == ("done", "done") and state_of(h, old[0]) == ("done", "done"),
                "both runs to finish",
                timeout=300,
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    finished = h.admin.execute(
        "select j.id, r.updated_at from app.pipeline_runs r join app.jobs j on j.id = r.job_id"
        " where r.case_id = %s order by r.updated_at",
        (h.case_id,),
    ).fetchall()
    assert finished[0][0] == old[0], "the newer run's tail ran before the older run's"
