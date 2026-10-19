"""F4 and F5: the OCR service failing on one page, or being busy (failure-modes.md).

A page the service cannot read becomes `unreadable` with no words and the job carries on (F4). A busy
service pauses the job with its reason, to be retried later, and does not fail it (F5). Messages name
pages and codes only; no page text is read or printed.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.ai.bedrock import ServiceBusy
from app.core.ai.ocr import OcrFailed, OcrModel, OcrResult
from app.core.ai.recordings import FixtureStore
from app.core.db import Database
from app.core.pdf import split_pages
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import Harness, recorded_book, subset_document, subset_entries
from tests.repository.test_pipeline_words import _open, recordings, storage  # noqa: F401
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


class Failing:
    """Recorded OCR that raises `error` for the pages whose SHA-256 is in `bad`, or for all when `bad` is None."""

    def __init__(self, inner: Any, error: Exception, bad: set[str] | None) -> None:
        self.inner, self.error, self.bad = inner, error, bad
        self.endpoint = inner.endpoint

    async def analyse(self, document: bytes, model: OcrModel = "layout") -> OcrResult:
        if self.bad is None or hashlib.sha256(document).hexdigest() in self.bad:
            raise self.error
        result: OcrResult = await self.inner.analyse(document, model)
        return result


def scanned_hashes() -> list[tuple[int, str]]:
    cut = split_pages(subset_document())
    return [
        (int(e["page"]), hashlib.sha256(cut[i]).hexdigest())
        for i, e in enumerate(subset_entries())
        if e["text_source"] == "ocr"
    ]


@pytest.fixture
async def opened(
    recordings: FixtureStore,  # noqa: F811
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,  # noqa: F811
) -> AsyncIterator[Harness]:
    ctx = await _open(
        LAUNCH,
        recordings,
        recorded_book(recordings),
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    )
    async with ctx as h:
        yield h


def _break(h: Harness, error: Exception, bad: set[str] | None) -> None:
    assert h.ocr is not None
    counting: Any = h.ocr
    counting.inner = Failing(counting.inner, error, bad)


def _count(h: Harness, page_id: Any) -> int:
    row = h.admin.execute("select count(*) from app.ocr_words where page_id = %s", (page_id,)).fetchone()
    assert row is not None
    return int(row[0])


async def test_a_page_ocr_cannot_read_is_unreadable_and_the_job_carries_on(opened: Harness) -> None:
    (broken_no, broken), *rest = scanned_hashes()
    _break(opened, OcrFailed("422"), {broken})
    await opened.upload(subset_document())
    finished = await opened.drain(timeout=300)
    assert set(finished.values()) == {"done"}, finished
    numbers = [int(e["page"]) for e in subset_entries()]
    pages = dict(zip(numbers, opened.pages(), strict=True))
    assert pages[broken_no]["unreadable"] is True
    assert _count(opened, pages[broken_no]["id"]) == 0
    for n, _ in rest:
        assert _count(opened, pages[n]["id"]) > 0, f"page {n} lost its words"


async def test_a_busy_ocr_service_pauses_the_job_and_does_not_fail_it(opened: Harness) -> None:
    _break(opened, ServiceBusy(), None)
    await opened.upload(subset_document())
    finished = await opened.drain(timeout=300)
    assert "failed" not in finished.values(), finished
    rows = opened.admin.execute(
        "select status, error_code from app.jobs where case_id = %s and kind = 'process_bundle'", (opened.case_id,)
    ).fetchall()
    assert rows and {r[0] for r in rows} == {"paused"}, rows
    assert {r[1] for r in rows} == {"service_busy"}, rows
