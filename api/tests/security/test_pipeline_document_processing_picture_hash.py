"""Audit 13, DP-09 follow-up: the duplicate check's picture decode runs in a killable child too.

The picture hash opens the stored thumbnail with Pillow: bytes drawn from a stranger's document. A stand-in that
sleeps past the timeout, or exits its own process, replaces that call. The page is left without a picture hash (so
it is never compared as a copy and is tried again on the next run), the job does not fail, and once the decode works
again the next run hashes it. Nothing printed or asserted on is patient text.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest

from app.core import sandbox
from app.core.config import SandboxSettings
from app.features.pipeline import parsing
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support import pipeline_parse_stand_ins as stand_in
from tests.support.file_lifecycle_documents import text_pdf

pytestmark = pytest.mark.anyio

h = lifecycle.h  # the same harness fixtures
storage = lifecycle.storage

TIMEOUT = 6.0


@pytest.fixture(autouse=True)
async def short_timeout() -> AsyncIterator[None]:
    await sandbox.start(SandboxSettings(pool_size=2, timeout_seconds=TIMEOUT))
    yield
    await sandbox.stop()


def hashes(h: Harness) -> list[object]:
    rows = h.admin.execute(
        "select a.phash from app.page_analysis a join app.pages p on p.id = a.page_id"
        " where a.bundle_id = (select bundle_id from app.cases where id = %s) order by p.bundle_page_no",
        (h.case_id,),
    ).fetchall()
    return [r[0] for r in rows]


@pytest.mark.parametrize("mode", ["hang", "exit"])
async def test_a_picture_decode_that_hangs_or_dies_leaves_the_page_unhashed_and_the_job_goes_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(parsing, "picture_hash", getattr(stand_in, f"picture_hash_{mode}"), raising=False)
    started = time.monotonic()
    await lifecycle.run(h, text_pdf(2, "alpha"), "two.pdf")  # run() fails unless every job finished "done"
    assert time.monotonic() - started < TIMEOUT * 10, "the job waited far past the sandbox timeout"
    assert hashes(h) == [None, None], "a page that could not be decoded was given a picture hash"
    pages = h.pages()
    assert [p["unreadable"] for p in pages] == [False, False], "the words were lost with the hash"

    monkeypatch.undo()  # the decode works again: the next run hashes the pages
    await h.enqueue("process_pages")
    assert set((await h.drain()).values()) == {"done"}
    assert all(value is not None for value in hashes(h)), "the pages were not tried again"
