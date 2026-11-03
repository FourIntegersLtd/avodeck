"""Per-kind limits and short transactions: 60 scanned pages through a worker pool of four connections.

OCR waits on Azure, so it runs 30 pages at once; that is only possible if no page holds a database
connection while it waits (the pool is small). The same goes for every S3 call."""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.storage import CaseFileStorage
from app.features.pipeline import service
from tests.repository.test_pipeline_fanout_support import PAGES, World, world
from tests.repository.test_pipeline_words import storage  # noqa: F401
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

WORDS = "select count(distinct page_id) from app.ocr_words where case_id = %s"


@pytest.fixture
async def scanned(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,  # noqa: F811
) -> AsyncIterator[World]:
    async with world(
        admin=admin, api_db=api_db, worker_dsn=worker_dsn, auth_env=auth_env, seed=seed, storage=storage
    ) as w:
        yield w


def test_each_kind_of_wait_has_its_own_limit_and_the_default_is_unchanged() -> None:
    assert service.OCR_CONCURRENCY == 30
    assert service.STORAGE_CONCURRENCY == 20
    assert service.CPU_CONCURRENCY == (os.cpu_count() or 2)
    assert inspect.signature(service.bounded).parameters["limit"].default == 3


async def test_splitting_puts_no_page_picture_while_holding_a_connection(scanned: World) -> None:
    await service.run_step(scanned.ctx, scanned.services, "split")
    assert scanned.count("select count(*) from app.pages p join app.cases c on c.bundle_id = p.bundle_id"
                         " where c.id = %s and p.image_path is not null") == PAGES
    assert scanned.store_watch.violations == []
    assert 3 < scanned.store_watch.peak <= service.STORAGE_CONCURRENCY


async def test_ocr_runs_thirty_pages_at_once_and_holds_no_connection_while_it_waits(scanned: World) -> None:
    await service.run_step(scanned.ctx, scanned.services, "split")
    scanned.store_watch.violations.clear()
    await service.run_step(scanned.ctx, scanned.services, "ocr")
    assert scanned.count(WORDS) == PAGES
    assert scanned.ocr_watch.peak == service.OCR_CONCURRENCY
    assert scanned.ocr_watch.violations == []
    assert scanned.store_watch.violations == []  # the page is fetched from S3 with no connection held


async def test_hashing_pages_runs_at_the_storage_limit_and_holds_no_connection_across_the_read(
    scanned: World,
) -> None:
    for step in ("split", "ocr"):
        await service.run_step(scanned.ctx, scanned.services, step)
    scanned.store_watch.violations.clear()
    scanned.store_watch.peak = 0
    await service.run_step(scanned.ctx, scanned.services, "duplicates")
    assert scanned.store_watch.violations == []
    assert 3 < scanned.store_watch.peak <= service.STORAGE_CONCURRENCY


async def test_a_second_run_asks_ocr_for_nothing_more(scanned: World) -> None:
    for step in ("split", "ocr"):
        await service.run_step(scanned.ctx, scanned.services, step)
    scanned.ocr_watch.peak = 0
    await service.run_step(scanned.ctx, scanned.services, "ocr")
    assert scanned.ocr_watch.peak == 0


async def test_one_summary_line_is_logged_for_every_step_run(
    scanned: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await service.run_step(scanned.ctx, scanned.services, "split")
    text = [r.getMessage() for r in caplog.records if "event=end" in r.getMessage()]
    assert len(text) == 1 and "step=split" in text[0]
