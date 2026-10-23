"""Slice G, the words of a page read as the expert's correction (slice B, 0018)."""

from __future__ import annotations

import pytest

from app.core.db import Database
from app.features.after_serving.common import page_words
from tests.support.corrected_world import CRYPTO, READING, corrected_world
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def test_page_words_read_as_the_experts_correction(seed: Seed, admin_dsn: str, api_db: Database) -> None:
    w = await corrected_world(seed, admin_dsn, api_db)
    async with api_db.request(scope_for(w.expert)) as conn:
        words = dict(await page_words(conn, CRYPTO, w.case, w.page_id))
    assert words[w.first] == READING
    assert words[w.second] == ""
    others = [k for k in w.original if k not in (w.first, w.second)]
    assert all(words[k] == w.original[k] for k in others)
