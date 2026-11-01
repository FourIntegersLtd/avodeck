"""Failure mode: the scanner cannot be reached. The document is not accepted (fail closed, not "clean").

`scan_document` raises `ScanUnavailable` out of the job so the queue retries it; no clean `document_scans`
row is written and `process_bundle` is never queued. When the scanner comes back, a scan accepts the file.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.scan import RecordedScanner, ScanResult, ScanUnavailable
from app.core.storage import CaseFileStorage
from app.features.pipeline import wiring
from tests.repository.test_pipeline_support import Harness, empty_answer, harness, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.files import local_storage
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


class Unreachable:
    async def scan(self, chunks: AsyncIterable[bytes]) -> ScanResult:
        async for _ in chunks:
            pass
        raise ScanUnavailable("scanner unreachable")


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
    case_id = seed.case(seed.org(members={expert: "owner"}), expert)
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([empty_answer("x")]),
    ) as running:
        yield running


def _scans(h: Harness) -> list[tuple[bool]]:
    return h.admin.execute("select clean from app.document_scans where bundle_id = (select bundle_id from app.cases where id = %s)", (h.case_id,)).fetchall()


async def test_an_unreachable_scanner_never_accepts_the_document(h: Harness) -> None:
    services = wiring.current()
    services.scanner = Unreachable()
    document = await h.upload(text_pdf([["Synthetic page"]]))
    finished = await h.drain(timeout=60)
    assert _scans(h) == [], "no row at all, least of all a clean one"
    kinds = [r[0] for r in h.admin.execute("select kind from app.jobs where case_id = %s", (h.case_id,))]
    assert "process_bundle" not in kinds
    assert h.pages() == []
    scan_status = h.admin.execute(
        "select status from app.jobs where case_id = %s and kind = 'scan_document'", (h.case_id,)
    ).fetchone()
    assert scan_status is not None and scan_status[0] != "done", finished
    assert document is not None


async def test_the_scan_runs_again_when_the_scanner_is_back(h: Harness) -> None:
    services = wiring.current()
    services.scanner = Unreachable()
    await h.upload(text_pdf([["Synthetic page"]]))
    await h.drain(timeout=60)
    services.scanner = RecordedScanner()
    again = await h.enqueue("scan_document")
    finished = await h.drain(timeout=60)
    assert finished[again] == "done"
    assert _scans(h) == [(True,)]
