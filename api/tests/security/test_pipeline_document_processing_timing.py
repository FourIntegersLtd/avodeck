"""Audit 13, DP-09: wall time of splitting a ~100-page synthetic PDF end to end (upload, count, cut, pictures, words).

The time is written to the test output as one line (`SPLIT100 <seconds>`) so it can be compared before and after the
parser sandbox. The only assertion is a generous ceiling, so a slow machine does not fail it.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest

from app.core import sandbox
from app.features.pipeline import SANDBOX_PREWARM
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support.file_lifecycle_documents import text_pdf

pytestmark = pytest.mark.anyio

h = lifecycle.h
storage = lifecycle.storage


@pytest.fixture(params=["cold", "prewarmed"])
async def pool(request: pytest.FixtureRequest) -> AsyncIterator[str]:
    """`prewarmed` is how the worker and the API start the pool; `cold` is children that start on first use."""
    await sandbox.start(prewarm=SANDBOX_PREWARM if request.param == "prewarmed" else ())
    yield str(request.param)
    await sandbox.stop()


async def test_a_hundred_page_document_is_split_within_a_generous_ceiling(h: Harness, pool: str) -> None:
    started = time.perf_counter()
    await lifecycle.run(h, text_pdf(100, "timed"), "hundred.pdf")
    seconds = time.perf_counter() - started
    print(f"SPLIT100 {pool} {seconds:.2f}")  # noqa: T201 - a measurement, no patient text
    assert len(h.pages()) == 100
    assert seconds < 300
