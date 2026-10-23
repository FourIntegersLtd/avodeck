"""Slice I, our pages' text for matching a trial bundle reads as the expert's correction (slice B, 0018)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.db import Database
from app.features.trial.jobs import our_pages
from tests.support.corrected_world import CRYPTO, READING, corrected_world
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


class _Services:
    def case_crypto(self) -> Any:
        return CRYPTO


async def test_our_pages_text_reads_as_the_experts_correction(seed: Seed, admin_dsn: str, api_db: Database) -> None:
    w = await corrected_world(seed, admin_dsn, api_db)
    async with api_db.request(scope_for(w.expert)) as conn:
        pages = await our_pages(_Services(), conn, w.case)  # type: ignore[arg-type]
    page = next(p for p in pages if p.key == w.page_id)
    words = page.text.split()
    assert words[0] == READING
    assert w.original[w.first] not in words[:1] and w.original[w.second] != words[1]
    assert len(words) == len(w.original) - 1
