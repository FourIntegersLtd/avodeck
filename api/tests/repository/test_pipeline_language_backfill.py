# ruff: noqa: F811
"""A page read before its language was worked out gets its language on the next run."""

from __future__ import annotations

import pytest

from app.core.contracts.pipeline import PROCESS_BUNDLE
from tests.repository.test_pipeline_language import h, storage  # noqa: F401
from tests.repository.test_pipeline_support import Harness

pytestmark = pytest.mark.anyio


async def test_pages_read_without_a_language_get_one_on_the_next_run(h: Harness) -> None:
    before = [p["language"] for p in h.pages()]
    assert before[0] and before[1] and before[2] is None
    h.admin.execute("update app.pages set language = null where bundle_id = (select bundle_id from app.cases where id = %s)", (h.case_id,))
    await h.enqueue(PROCESS_BUNDLE)
    await h.drain()
    assert [p["language"] for p in h.pages()] == before, "a page with enough words was left without a language"
