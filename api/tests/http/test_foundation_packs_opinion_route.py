"""GET /v1/cases/{case_id}/pack carries the opinion words (slice E), under both pack combinations."""

from __future__ import annotations

import pytest

from tests.http.test_foundation_packs_routes import app, case_pack, cell_region  # noqa: F401
from tests.support.db import ClientFor
from tests.support.packs import BOTH, FIXTURE, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_the_case_pack_carries_the_opinion_words(client_for: ClientFor, seed: Seed, combo: Combo) -> None:
    body = await case_pack(client_for, seed, combo)
    raw = combo.raw("case-type", "opinion.yaml")
    assert [s["code"] for s in body["point_sides"]] == ["supports_claim", "supports_clinician", "neutral"]
    assert [v["code"] for v in body["opinion_views"]] == [v["code"] for v in raw["opinion_views"]]
    assert all(s["label"] and "{" not in s["label"] for s in body["point_sides"] + body["opinion_views"])
    assert body["range_note_prompt"] and "{" not in body["range_note_prompt"]
    assert "loaded_language" not in body


async def test_the_opinion_words_differ_between_launch_and_fixture(client_for: ClientFor, seed: Seed) -> None:
    launch, fixture = await case_pack(client_for, seed, LAUNCH), await case_pack(client_for, seed, FIXTURE)
    for field in ("point_sides", "opinion_views", "range_note_prompt"):
        assert launch[field] != fixture[field], field
