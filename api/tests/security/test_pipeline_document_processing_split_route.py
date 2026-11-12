"""Audit 13, DP-09 follow-up: the two-page-scan cut runs in a killable child, like every other parser call.

`POST /v1/cases/{id}/pages/{page_id}/split` runs in the API process, so a cut that hangs or kills its process must
not take the API with it. A stand-in that sleeps past the timeout, or exits its own process, replaces the cut for
one marked page. The request then fails with the route's normal error shape (a code, no page text), nothing is
half-written (no new document, no stored object, no queued job, no audit row), and the API serves the next request.
"""

from __future__ import annotations

import time
from typing import Any

import pikepdf
import pytest
from httpx import Response

from app.core import sandbox
from app.core.config import SandboxSettings
from app.features.pipeline import parsing, paths
from tests.repository.test_pipeline_quarantine import audited
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support import pipeline_parse_stand_ins as stand_in
from tests.support.file_lifecycle_documents import _save, _typed_page

pytestmark = pytest.mark.anyio

h = lifecycle.h  # the same harness fixtures
storage = lifecycle.storage

CUT = {"axis": "vertical", "at": 0.5, "first": "left"}
MODES = ["hang", "exit"]
TIMEOUT = 6.0


@pytest.fixture(autouse=True)
async def short_timeout() -> Any:
    await sandbox.start(SandboxSettings(pool_size=2, timeout_seconds=TIMEOUT))
    yield
    await sandbox.stop()


def two_pages() -> bytes:
    pdf = pikepdf.new()
    for words in ("keep one", f"{stand_in.MARK} two"):
        _typed_page(pdf, words)
    return _save(pdf)


async def cut(h: Harness, bundle_page_no: int) -> Response:
    page = next(p for p in h.pages() if p["bundle_page_no"] == bundle_page_no)
    return await h.client.post(f"/v1/cases/{h.case_id}/pages/{page['id']}/split", json=CUT)


def written(h: Harness) -> tuple[int, int, int]:
    documents = h.admin.execute(
        "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)",
        (h.case_id,),
    ).fetchone()
    jobs = h.admin.execute("select count(*) from app.jobs where case_id = %s", (h.case_id,)).fetchone()
    assert documents is not None and jobs is not None
    return int(documents[0]), int(jobs[0]), audited(h, "page.split")


@pytest.mark.parametrize("mode", MODES)
async def test_a_cut_that_hangs_or_dies_fails_with_a_code_and_writes_nothing(
    h: Harness, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    await lifecycle.run(h, two_pages(), "two.pdf")
    before = written(h)
    stored = await h.storage.list_recursive(f"cases/{h.case_id}/")
    monkeypatch.setattr(parsing, "halves", getattr(stand_in, f"halves_{mode}"), raising=False)

    started = time.monotonic()
    got = await cut(h, 2)  # the marked page
    took = time.monotonic() - started

    assert got.status_code == 422, got.status_code
    assert got.json()["error"]["code"] == "invalid"
    assert took < TIMEOUT * 3, "the request waited far past the sandbox timeout"
    assert written(h) == before, "a failed cut left a document, a job or an audit row behind"
    assert sorted(await h.storage.list_recursive(f"cases/{h.case_id}/")) == sorted(stored), "an object was stored"
    assert paths.page_pdf_path(h.case_id, next(p["id"] for p in h.pages())) in stored, "control: pages are stored"


@pytest.mark.parametrize("mode", MODES)
async def test_the_api_still_serves_and_cuts_the_next_page_after_a_failed_cut(
    h: Harness, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    await lifecycle.run(h, two_pages(), "two.pdf")
    monkeypatch.setattr(parsing, "halves", getattr(stand_in, f"halves_{mode}"), raising=False)
    assert (await cut(h, 2)).status_code == 422
    summary = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert summary.status_code == 200, "the API stopped answering after a failed cut"
    good = await cut(h, 1)  # the unmarked page, same stand-in, same pool
    assert good.status_code == 202, good.status_code
    assert written(h)[2] == 1
