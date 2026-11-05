"""Slice A: the split keeps the worker's memory bounded (scalability plan PR 6).

- A 1,500-page document is cut with a small, steady amount of Python memory: its parts go to a temp file
  as they are read (never one big `bytes`), each page is cut, uploaded and dropped (no all-pages cache).
- The cut is unchanged: every stored page PDF is byte-for-byte what core `split_pages` gives.
- One split runs at a time in a process, and the temp file is deleted whether the split succeeds or fails.
Everything is synthetic random bytes; nothing here prints or asserts on page text (rule 6).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import random
import tempfile
import time
import tracemalloc
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pikepdf
import psycopg
import pytest

from app.core.db import Database
from app.core.jobs import JobContext
from app.core.pdf import split_pages
from app.core.storage import CaseFileStorage
from app.features.pipeline import paths, service
from app.features.pipeline.render import Pictures
from tests.repository.test_pipeline_support import Harness, harness
from tests.support.files import local_storage
from tests.support.jobs import open_worker
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

MB = 1024 * 1024
PAGES = 1500
BALLAST = 48 * 1024  # random bytes carried by each page, so the document weighs about 72 MB
# 40% of the 146 MB the all-in-memory split added at its peak on this 72 MB document (measured before the change;
# docs/ops/capacity.md). One 8 MB stored part, sealed and opened, is most of what the new split holds.
PEAK_LIMIT = 58 * MB


def heavy_pdf(pages: int, ballast: int) -> bytes:
    """A PDF of blank pages, each carrying `ballast` random bytes that no compressor can shrink."""
    rng = random.Random(7)
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(200, 200))
        pdf.pages[-1].obj["/Ballast"] = pdf.make_stream(rng.randbytes(ballast))
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


class PageDiscarding:
    """The real storage, except the page objects the split writes are counted and dropped (a real bucket
    is not in this process's memory), so the measurement sees only what the split itself holds."""

    def __init__(self, real: CaseFileStorage) -> None:
        self.real = real
        self.drop = False
        self.dropped = 0

    async def put(self, path: str, data: bytes) -> None:
        if self.drop and "/pages/" in path:
            self.dropped += 1
            return
        await self.real.put(path, data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


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
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Harness, PageDiscarding]]:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert)
    spy = PageDiscarding(storage)

    async def only_split(ctx: JobContext, services: Any, step: str) -> None:
        if step == "split":
            await service._split(ctx, services)

    async def nothing(ctx: JobContext, services: Any) -> None:
        return None

    monkeypatch.setattr(service, "run_step", only_split)
    monkeypatch.setattr(service, "_ready", nothing)
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=spy,  # type: ignore[arg-type]
        expert=expert,
        case_id=case_id,
    ) as running:
        yield running, spy


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the split's temp files go (the system temp directory is read when a file is made)."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


async def no_pictures(page_pdf: bytes, rotation: int) -> Pictures:
    return Pictures(image=b"i", thumbnail=b"t")


async def finish(run: Harness, timeout: float = 120) -> dict[uuid.UUID, str]:
    """Runs the worker until every job on the case is `done` or `failed`. (`Harness.drain` counts `paused` as
    finished, and a job the worker was stopped under is paused, so it can return before a slow job is done.)"""
    deadline = time.monotonic() + timeout
    async with open_worker(run.registry, run.worker_db, run.worker_dsn) as worker:
        task = asyncio.create_task(worker.run())
        try:
            while True:
                rows = run.admin.execute("select id, status from app.jobs where case_id = %s", (run.case_id,))
                found = {r[0]: r[1] for r in rows.fetchall()}
                if found and all(s in ("done", "failed") for s in found.values()):
                    return found
                assert time.monotonic() < deadline, f"jobs not finished in {timeout}s: {sorted(found.values())}"
                await asyncio.sleep(0.2)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def with_measure(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Wrap the split so the Python memory it adds at its peak is recorded."""
    seen: dict[str, int] = {}
    original = service._split

    async def measured(ctx: JobContext, services: Any) -> None:
        tracemalloc.start()
        base, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        try:
            await original(ctx, services)
        finally:
            seen["peak"] = tracemalloc.get_traced_memory()[1] - base
            tracemalloc.stop()

    monkeypatch.setattr(service, "_split", measured)
    return seen


async def test_splitting_1500_pages_keeps_python_memory_low(
    h: tuple[Harness, PageDiscarding], scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, spy = h
    monkeypatch.setattr(service, "_pictures", no_pictures)
    document = heavy_pdf(PAGES, BALLAST)
    await run.upload(document)
    del document
    seen = with_measure(monkeypatch)
    spy.drop = True

    found = await finish(run, timeout=600)

    states = run.admin.execute("select kind, status, error_code from app.jobs where case_id = %s", (run.case_id,))
    assert set(found.values()) == {"done"}, states.fetchall()
    assert spy.dropped == PAGES * 3, "not every page was cut and stored"
    assert 0 < seen["peak"] < PEAK_LIMIT, f"the split's peak was {seen['peak'] // MB} MB"
    assert not list(scratch.iterdir()), "a temp file was left behind"


async def test_the_cut_is_the_same_bytes_as_before(h: tuple[Harness, PageDiscarding], scratch: Path) -> None:
    run, _ = h
    document = heavy_pdf(40, 2048)
    await run.upload(document)
    await finish(run)

    expected = [hashlib.sha256(p).hexdigest() for p in split_pages(document)]
    rows = run.admin.execute(
        "select id, bundle_page_no, image_path from app.pages where bundle_id ="
        " (select bundle_id from app.cases where id = %s) order by bundle_page_no",
        (run.case_id,),
    ).fetchall()
    assert len(rows) == len(expected) and all(r[2] for r in rows)
    for (page_id, number, _), want in zip(rows, expected, strict=True):
        path = paths.page_pdf_path(run.case_id, page_id)
        stored = await run.decrypt(await run.storage.get(path), path)
        assert hashlib.sha256(stored).hexdigest() == want, f"page {number} was cut differently"
    assert not list(scratch.iterdir()), "a temp file was left behind"


async def test_a_failed_split_still_deletes_its_temp_file(
    h: tuple[Harness, PageDiscarding], scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = h

    async def broken(page_pdf: bytes, rotation: int) -> Pictures:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(service, "_pictures", broken)
    await run.upload(heavy_pdf(6, 512))
    await finish(run, timeout=60)  # the job fails after its attempts; either way the file must be gone

    assert not list(scratch.iterdir()), "a temp file was left behind after a failure"


class FakeContext:
    """Enough of a job for a split that finds nothing to do."""

    case_id = uuid.uuid4()
    job_id = uuid.uuid4()

    def transaction(self) -> Any:
        @contextlib.asynccontextmanager
        async def one() -> AsyncIterator[None]:
            yield None

        return one()


async def test_only_one_split_runs_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    inside = 0
    overlap = False

    async def nothing_waiting(conn: Any, case_id: uuid.UUID) -> Sequence[Any]:
        nonlocal inside, overlap
        inside += 1
        overlap = overlap or inside > 1
        await asyncio.sleep(0.05)
        inside -= 1
        return []

    monkeypatch.setattr(service.repo, "clean_unsplit", nothing_waiting)
    monkeypatch.setattr(service.repo, "pages_without_image", nothing_waiting)
    ctx: Any = FakeContext()
    await asyncio.gather(*(service._split(ctx, None) for _ in range(3)))  # type: ignore[arg-type]
    assert not overlap, "two splits ran at once in one process"
