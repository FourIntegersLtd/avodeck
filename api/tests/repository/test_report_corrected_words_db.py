"""Slice F, colleague spot-check reads a page's words as the expert's reading (slice B, 0018)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from app.core.db import Database
from app.features.report.spot_checks import _page
from tests.support.corrected_world import CRYPTO, READING, corrected_world
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def test_the_spot_check_page_words_read_as_the_experts_correction(
    seed: Seed, admin_dsn: str, api_db: Database
) -> None:
    w = await corrected_world(seed, admin_dsn, api_db)
    async with api_db.request(scope_for(w.expert)) as conn:
        _, _, words, polygons = await _page(conn, CRYPTO, cast(Any, None), w.case, w.page_id)
    text = dict(words)
    assert text[w.first] == READING
    assert text[w.second] == ""
    assert polygons[w.first] and polygons[w.second]  # the crop still finds the cited words' boxes
    others = [k for k in w.original if k not in (w.first, w.second)]
    assert all(text[k] == w.original[k] for k in others)


async def test_a_name_in_a_folded_word_is_still_blacked_out(seed: Seed, admin_dsn: str, api_db: Database) -> None:
    from app.features.report.spot_checks import hidden_words

    w = await corrected_world(seed, admin_dsn, api_db)
    name = w.original[w.second]  # the party name, misread across the run, sits in its non-first word
    async with api_db.request(scope_for(w.expert)) as conn:
        hidden = await hidden_words(conn, CRYPTO, w.case, w.page_id, [name], lambda line: [])
    assert w.second in hidden  # masked on its original text, though the correction folded it away


async def test_a_name_in_the_corrected_text_hides_the_whole_run(seed: Seed, admin_dsn: str, api_db: Database) -> None:
    from app.features.report.spot_checks import hidden_words

    w = await corrected_world(seed, admin_dsn, api_db)
    async with api_db.request(scope_for(w.expert)) as conn:
        hidden = await hidden_words(conn, CRYPTO, w.case, w.page_id, [READING], lambda line: [])
    assert {w.first, w.second} <= hidden
