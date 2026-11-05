"""Slice A and foundation: the worker's loop stays free, and a stall is not a failure (scalability plan PR 5).

- PDFium and the slow page work run off the event loop, so the worker's heartbeat keeps beating during them.
- A job put back after its worker stalled keeps its `attempts`; a job that raised still adds one.
- On a stop signal the worker takes no new jobs and, once the grace has run out, marks running jobs `paused`
  (code `resuming`, no attempt counted), so they carry on when a worker starts again.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pikepdf
import psycopg
import pytest
from PIL import Image

from app.core.config import ConfigError
from app.core.db import Database
from app.core.jobs import JobContext
from app.features.pipeline import service
from app.worker.main import build_worker
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def blank_page() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def procrastinate_admin(admin_dsn: str) -> psycopg.Connection:
    return psycopg.connect(admin_dsn, autocommit=True, options="-c search_path=procrastinate")


async def enqueue(api_db: Database, seed: Seed, registry: Any, kind: str) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    async with api_db.request(scope_for(expert)) as conn:
        return case, await registry.enqueue(conn, case, kind)


def row(admin: psycopg.Connection, job: uuid.UUID) -> tuple[Any, ...]:
    found = admin.execute("select status, attempts, error_code from app.jobs where id = %s", (job,)).fetchone()
    assert found is not None
    return found


async def test_the_heartbeat_keeps_beating_while_a_page_is_drawn(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_rasterise(page_pdf: bytes, rotation: int = 0) -> Image.Image:
        time.sleep(2)  # a big scan: PDFium holds its thread, not the loop's
        return Image.new("RGB", (20, 20), "white")

    monkeypatch.setattr(service, "rasterise", slow_rasterise)
    registry = isolated_registry()
    beats: list[Any] = []

    @registry.kind("zz_draw")
    async def draw(ctx: JobContext) -> None:
        def heartbeat() -> Any:
            found = admin.execute(
                "select w.last_heartbeat from procrastinate.procrastinate_workers w"
                " join procrastinate.procrastinate_jobs j on j.worker_id = w.id where j.args ->> 'job_id' = %s",
                (str(ctx.job_id),),
            ).fetchone()
            assert found is not None
            return found[0]

        await asyncio.sleep(0.5)  # the first beats have been made
        beats.append(heartbeat())
        await service._pictures(blank_page(), 0)
        beats.append(heartbeat())

    _, job = await enqueue(api_db, seed, registry, "zz_draw")
    async with open_worker(registry, worker_db, worker_dsn, heartbeat_seconds=0.2) as worker:
        assert (await run_until_finished(worker, admin, [job], timeout=20))[job] == "done"

    assert beats[1] > beats[0], "the heartbeat stopped while the page was drawn"


async def test_pdfium_is_used_by_one_thread_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    inside: list[int] = []
    overlap: list[bool] = []

    def watch(page_pdf: bytes, rotation: int = 0) -> Image.Image:
        inside.append(1)
        overlap.append(len(inside) > 1)
        time.sleep(0.05)
        inside.pop()
        return Image.new("RGB", (20, 20), "white")

    monkeypatch.setattr(service, "rasterise", watch)
    await asyncio.gather(*(service._pictures(blank_page(), 0) for _ in range(6)))
    assert not any(overlap), "PDFium is not thread-safe: two calls ran at once"


async def test_a_job_put_back_after_a_stall_keeps_its_attempts(
    api_db: Database, worker_db: Database, worker_dsn: str, admin_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    seen: list[int] = []

    @registry.kind("zz_stalled")
    async def stalled(ctx: JobContext) -> None:
        seen.append(ctx.attempt)

    _, job = await enqueue(api_db, seed, registry, "zz_stalled")
    with procrastinate_admin(admin_dsn) as q:
        dead = q.execute("select procrastinate_register_worker_v1()").fetchone()[0]  # type: ignore[index]
        q.execute("select id from procrastinate_fetch_job_v2(%s, %s)", ([registry.queue], dead))
        q.execute("update procrastinate_workers set last_heartbeat = now() - interval '1 hour' where id = %s", (dead,))
    admin.execute("update app.jobs set status = 'running', attempts = 1, started_at = now() where id = %s", (job,))

    async with open_worker(registry, worker_db, worker_dsn, stalled_after_seconds=60) as worker:
        assert await worker.requeue_stalled() == 1
        assert row(admin, job)[0] == "running"
        await run_until_finished(worker, admin, [job])

    assert seen == [1], "a stall counted as an attempt"
    assert row(admin, job) == ("done", 1, None)
    flag = admin.execute("select requeued_stall from app.jobs where id = %s", (job,)).fetchone()
    assert flag == (False,), "the stall flag is cleared when the job starts"


async def test_a_job_that_raised_still_adds_an_attempt(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    seen: list[int] = []

    @registry.kind("zz_raises")
    async def raises(ctx: JobContext) -> None:
        seen.append(ctx.attempt)
        if ctx.attempt == 1:
            raise RuntimeError("boom")

    _, job = await enqueue(api_db, seed, registry, "zz_raises")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_finished(worker, admin, [job])

    assert seen == [1, 2]
    assert row(admin, job) == ("done", 2, None)


async def test_on_a_stop_running_jobs_are_paused_without_an_attempt_and_carry_on_at_restart(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    seen: list[int] = []
    started = asyncio.Event()
    hold = {"on": True}

    @registry.kind("zz_long")
    async def long(ctx: JobContext) -> None:
        seen.append(ctx.attempt)
        if hold["on"]:
            started.set()
            await asyncio.sleep(60)

    _, job = await enqueue(api_db, seed, registry, "zz_long")
    async with open_worker(registry, worker_db, worker_dsn, shutdown_grace_seconds=0.3) as worker:
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=20)
        stopped = time.monotonic()
        task.cancel()  # what the stop signal does: the worker takes no more jobs and drains
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=15)
        assert time.monotonic() - stopped < 10, "the worker did not stop within its grace"
        assert row(admin, job) == ("paused", 1, "resuming")

        hold["on"] = False
        assert (await run_until_finished(worker, admin, [job]))[job] == "done"

    assert seen == [1, 1], "a stop counted as an attempt"
    assert row(admin, job) == ("done", 1, None)


async def test_the_shutdown_grace_comes_from_the_environment() -> None:
    env = {
        "DATABASE_URL": "postgresql://app_api:x@127.0.0.1:1/postgres",
        "DATABASE_URL_DIRECT": "postgresql://app_worker:x@127.0.0.1:1/postgres",
    }
    assert build_worker(env, features="app.features").shutdown_grace_seconds == 25
    env["WORKER_SHUTDOWN_GRACE_SECONDS"] = "40"
    assert build_worker(env, features="app.features").shutdown_grace_seconds == 40
    env["WORKER_SHUTDOWN_GRACE_SECONDS"] = "soon"
    with pytest.raises(ConfigError):
        build_worker(env, features="app.features")
