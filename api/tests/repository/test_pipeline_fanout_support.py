"""Stand-ins for the fan-out tests: a 60-page scanned file, an OCR that counts what is in flight, a store that
notices a database connection held across a call, and a worker pool of four connections (so 30 tasks
holding one each would starve it)."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import uuid
from collections.abc import AsyncIterator, Mapping
from contextvars import ContextVar
from typing import Any

import pikepdf
import psycopg

from app.core.ai.ocr import OcrModel, OcrPage, OcrResult, OcrWord
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.storage import CaseFileStorage
from app.features import pipeline
from app.features.pipeline.wiring import PipelineServices, current
from tests.repository.test_pipeline_support import Harness, harness
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

PAGES = 60
SECRET_NAME = "Jane Testpatient"
SECRET_NUMBER = "943 476 5919"

# True while the current task holds a worker connection (set around `Database.job`).
holding: ContextVar[bool] = ContextVar("holding", default=False)


def scanned_document(count: int = PAGES) -> bytes:
    """Blank pages with no text layer (so each goes to OCR); page n is 600+n points wide."""
    pdf = pikepdf.new()
    for n in range(1, count + 1):
        pdf.add_blank_page(page_size=(600 + n, 800))
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


class Watch:
    """In-flight count, its peak, and every call made while the task held a database connection."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0
        self.violations: list[str] = []

    @contextlib.asynccontextmanager
    async def call(self, what: str) -> AsyncIterator[None]:
        if holding.get():
            self.violations.append(what)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.02)
            yield
        finally:
            self.in_flight -= 1


class WatchedStore:
    def __init__(self, inner: CaseFileStorage, watch: Watch) -> None:
        self.inner, self.watch = inner, watch

    async def get(self, path: str) -> bytes:
        async with self.watch.call("storage.get"):
            return await self.inner.get(path)

    async def put(self, path: str, data: bytes, *args: Any, **kwargs: Any) -> Any:
        async with self.watch.call("storage.put"):
            return await self.inner.put(path, data, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class FakeOcr:
    """Reads page n of `scanned_document` as three words; `failing` maps a page number to its error."""

    endpoint = "https://ocr.example.test"

    def __init__(self, watch: Watch, failing: Mapping[int, Exception] | None = None) -> None:
        self.watch = watch
        self.failing = dict(failing or {})

    async def analyse(self, document: bytes, model: OcrModel = "layout") -> OcrResult:
        with pikepdf.open(io.BytesIO(document)) as pdf:
            number = int(float(pdf.pages[0].mediabox[2])) - 600
        async with self.watch.call("ocr"):
            if number in self.failing:
                raise self.failing[number]
            words = tuple(
                OcrWord(id=f"p1w{k}", text=text, polygon=(0, 0, 1, 0, 1, 1, 0, 1), confidence=0.99)
                for k, text in enumerate([SECRET_NAME, SECRET_NUMBER, f"page {number} of the record"], start=1)
            )
        return OcrResult(model="layout", pages=(OcrPage(number=1, width=8.5, height=11.0, words=words),))


@dataclasses.dataclass
class World:
    h: Harness
    ctx: JobContext
    services: PipelineServices
    ocr: FakeOcr
    ocr_watch: Watch
    store_watch: Watch
    held_peak: list[int]

    def count(self, sql: str) -> int:
        row = self.h.admin.execute(sql, (self.h.case_id,)).fetchone()
        assert row is not None
        return int(row[0])


@contextlib.asynccontextmanager
async def world(
    *,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
    failing: Mapping[int, Exception] | None = None,
) -> AsyncIterator[World]:
    from app.features.pipeline import service

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
        ocr_store=None,
    ) as h:
        ocr_watch, store_watch = Watch(), Watch()
        ocr = FakeOcr(ocr_watch, failing)
        services = dataclasses.replace(current(), ocr=ocr, storage=WatchedStore(storage, store_watch))
        live = h.worker_db.job
        open_now = [0]
        peak = [0]

        @contextlib.asynccontextmanager
        async def tracked(case: uuid.UUID) -> AsyncIterator[Any]:
            token = holding.set(True)
            open_now[0] += 1
            peak[0] = max(peak[0], open_now[0])
            try:
                async with live(case) as conn:
                    yield conn
            finally:
                open_now[0] -= 1
                holding.reset(token)

        h.worker_db.job = tracked  # type: ignore[method-assign]
        ctx = JobContext(h.worker_db, uuid.uuid4(), case_id, "process_bundle", 1)
        await h.upload(scanned_document())
        with pipeline.wiring.use(services):
            await service.scan_document(ctx)
            yield World(h, ctx, services, ocr, ocr_watch, store_watch, peak)
