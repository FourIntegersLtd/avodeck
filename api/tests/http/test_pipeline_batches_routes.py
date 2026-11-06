"""The routes S07 and S08 follow, with page-batch jobs on (ADR 0038, build step 12).

`GET /process` and the summary show the parent only, with the same status values as before, while children run, after
a child failed for good and after Try again; `POST /process/retry` puts back the parent and only the failed batch is
read again; `POST /process/cancel` works while the parent is parked. Counts, ids and codes only (rule 6).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.config import PipelineBatchSettings
from app.core.db import Database
from app.core.jobs import RESUMABLE_PAUSES
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
PARENTS = ("process_bundle", "process_pages")


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


async def until(check: Callable[[], bool], what: str, *, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.05)


def parent_id(h: Harness) -> uuid.UUID:
    row = h.admin.execute(
        "select id from app.jobs where case_id = %s and kind = any(%s) order by created_at limit 1",
        (h.case_id, list(PARENTS)),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


async def processing(h: Harness) -> dict[str, Any]:
    got = await h.client.get(f"/v1/cases/{h.case_id}/process")
    assert got.status_code == 200
    job = got.json()["job"]
    assert job is not None
    return dict(job)


async def test_the_status_routes_show_the_parent_while_children_run_and_cancel_works_while_parked(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    async with opened(recordings, admin, api_db, worker_dsn, auth_env, seed, storage) as h:
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
                parent = parent_id(h)
                job = await processing(h)
                assert uuid.UUID(str(job["id"])) == parent and job["kind"] in PARENTS
                assert job["status"] == "running" and job["step"] == "ocr"
                summary = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
                assert summary.status_code == 200
                stopped = await h.client.post(f"/v1/cases/{h.case_id}/process/cancel")
                assert stopped.status_code == 202 and stopped.json() == {"cancelled": True}
            finally:
                release.set()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


async def test_try_again_after_a_child_failed_puts_back_the_parent_and_reads_only_that_batch(
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
        counts = Counter(clean.ocr.sent)
        sent_once = [sha for sha, n in counts.items() if n == 1]
        baseline = len(clean.ocr.sent)
    assert sent_once, "the recorded subset has no page that is read exactly once"
    broken_sha = sent_once[-1]
    async with opened(recordings, admin, api_db, worker_dsn, auth_env, seed, storage) as h:
        assert h.ocr is not None
        real = h.ocr.inner.analyse
        broken = {"on": True}

        async def flaky(document: bytes, *args: Any, **kwargs: Any) -> Any:
            import hashlib

            if broken["on"] and hashlib.sha256(document).hexdigest() == broken_sha:
                raise RuntimeError("this page cannot be read")
            return await real(document, *args, **kwargs)

        h.ocr.inner.analyse = flaky  # type: ignore[method-assign]
        await h.upload(subset_document())
        async with open_worker(h.registry, h.worker_db, h.worker_dsn) as worker:
            task = asyncio.create_task(worker.run(concurrency=3))
            try:
                await until(
                    lambda: h.admin.execute(
                        "select 1 from app.jobs where case_id = %s and kind = any(%s)", (h.case_id, list(PARENTS))
                    ).fetchone()
                    is not None,
                    "the bundle job to be queued",
                )
                parent = parent_id(h)

                def a_child_failed() -> bool:
                    row = h.admin.execute(
                        "select count(*) from app.jobs where case_id = %s and kind = 'read_page_batch'"
                        " and status = 'failed'",
                        (h.case_id,),
                    ).fetchone()
                    return row is not None and row[0] >= 1

                await until(a_child_failed, "a child to fail for good", timeout=300)
                # off the event loop: the sweep may wait for a row lock the worker (same loop) holds
                await asyncio.to_thread(
                    h.admin.execute, "select app.pipeline_heal_runs(%s)", (sorted(RESUMABLE_PAUSES),)
                )
                await until(
                    lambda: h.admin.execute("select status from app.jobs where id = %s", (parent,)).fetchone()
                    == ("failed",),
                    "the sweep to fail the parent",
                )
                job = await processing(h)
                assert uuid.UUID(str(job["id"])) == parent and job["status"] == "failed"
                before = len(h.ocr.sent)
                broken["on"] = False
                retried = await h.client.post(f"/v1/cases/{h.case_id}/process/retry")
                assert retried.status_code == 200 and retried.json()["requeued"] == 1
                await until(
                    lambda: h.admin.execute("select status from app.jobs where id = %s", (parent,)).fetchone()
                    == ("done",),
                    "the parent to finish after Try again",
                    timeout=300,
                )
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        assert len(h.ocr.sent) - before <= ON.pages, "more than the failed batch was read again"
        assert len(h.ocr.sent) <= baseline + 3 * ON.pages
        job = await processing(h)
        assert job["status"] == "done"
