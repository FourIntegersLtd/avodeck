"""Screen 11 v2 shapes (docs/design/timeline-v2-followups.md, "Seam"): the timeline reply carries `gaps`,
each lane its `series` and each event its `series_key`; `GET /timeline/overview` answers for the ribbon
with the same access check as the timeline. The parts that fill these in land in their own streams.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.http.test_understand_routes_timeline import Case, _plain_lanes
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_the_timeline_carries_gaps_series_and_series_keys(
    combo: Combo, client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, combo)
    case.fact(_plain_lanes(case.pack)[0]["kinds"][0], date(2024, 5, 1))
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["gaps"], list)
    assert all(isinstance(lane["series"], list) for lane in body["lanes"])
    events = [e for lane in body["lanes"] for e in lane["events"]]
    assert events and all("series_key" in e for e in events)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_the_overview_answers_with_its_shape(combo: Combo, client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed, combo)
    resp = await (await client_for(case.assistant)).get(
        f"/v1/cases/{case.id}/timeline/overview", params={"as_seen_on": "2024-06-01"}
    )
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) >= {"range", "months", "lanes", "pins"}


async def test_the_overview_is_404_for_someone_outside_the_case(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    assert (await (await client_for(case.stranger)).get(f"/v1/cases/{case.id}/timeline/overview")).status_code == 404
