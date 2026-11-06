"""A child that dies part-way is retried and redoes only what is missing (F55; ADR 0038, build step 11).

The OCR stand-in fails once on its third call (the child is "killed" after two pages of its batch); the retry finds the
two pages' words and asks the service only for the rest. Counts, ids and codes only (rule 6).
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
FAIL_ON_CALL = 3


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


@contextlib.asynccontextmanager
async def opened(
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
    async with opened(recordings, admin, api_db, worker_dsn, auth_env, seed, storage) as harnessed:
        yield harnessed


async def until(check: Callable[[], bool], what: str, *, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.05)


async def test_cancel_then_start_closes_the_old_run_and_finishes_what_is_left(h: Harness) -> None:
    """The old parent is still `running` and parked (the sweep has not mirrored the stop); the new parent's first act
    takes the run over, reads only what is left and finishes."""
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
            old = h.admin.execute(
                "select job_id from app.pipeline_runs where case_id = %s", (h.case_id,)
            ).fetchone()
            assert old is not None
            stopped = await h.client.post(f"/v1/cases/{h.case_id}/process/cancel")
            assert stopped.status_code == 202 and stopped.json() == {"cancelled": True}
            release.set()
            started = await h.client.post(f"/v1/cases/{h.case_id}/process")
            assert started.status_code == 202
            new = uuid.UUID(started.json()["job_id"])
            await until(
                lambda: h.admin.execute("select status from app.jobs where id = %s", (new,)).fetchone() == ("done",),
                "the new parent to finish",
                timeout=300,
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    runs = h.admin.execute(
        "select r.job_id, r.state, r.closed_by_job_id, j.status from app.pipeline_runs r"
        " join app.jobs j on j.id = r.job_id where r.case_id = %s order by r.created_at",
        (h.case_id,),
    ).fetchall()
    assert [r[1] for r in runs] == ["closed", "done"], runs
    assert runs[0][0] == old[0] and runs[0][2] == new and runs[0][3] != "running"
    scanned = h.admin.execute(
        "select count(*) from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)"
        " and text_source = 'ocr'",
        (h.case_id,),
    ).fetchone()
    assert scanned is not None
    # only the calls in flight at the stop may be made twice (their run closed before they wrote)
    assert scanned[0] <= len(h.ocr.sent) <= scanned[0] + 4, "pages already read were read again after the stop"
    read = h.admin.execute(
        "select count(*) from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)"
        " and text_source is not null",
        (h.case_id,),
    ).fetchone()
    assert read == (PAGES,)


async def test_a_retried_child_asks_the_ocr_service_only_for_the_pages_still_missing(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    async with opened(recordings, admin, api_db, worker_dsn, auth_env, seed, storage) as clean:
        await clean.upload(subset_document())
        assert set((await clean.drain(timeout=300)).values()) == {"done"}
        assert clean.ocr is not None
        baseline = len(clean.ocr.sent)
    async with opened(recordings, admin, api_db, worker_dsn, auth_env, seed, storage) as h:
        assert h.ocr is not None
        real = h.ocr.inner.analyse
        calls = {"n": 0}

        async def flaky(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == FAIL_ON_CALL:
                raise RuntimeError("the worker was killed here")
            return await real(*args, **kwargs)

        h.ocr.inner.analyse = flaky  # type: ignore[method-assign]
        await h.upload(subset_document())
        finished = await h.drain(timeout=300)
        assert set(finished.values()) == {"done"}, finished
        assert baseline > FAIL_ON_CALL
        # The pages in flight when the child died are asked again; the ones it had finished are not, and no other
        # batch is read twice: the extra calls stay inside one batch.
        extra = len(h.ocr.sent) - baseline
        assert 1 <= extra < ON.pages, f"{extra} extra OCR calls: the retry did not skip the pages already read"
        read = h.admin.execute(
            "select count(*) from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)"
            " and text_source is not null",
            (h.case_id,),
        ).fetchone()
        assert read == (PAGES,)
