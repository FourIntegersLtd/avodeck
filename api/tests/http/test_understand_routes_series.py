"""The timeline carries each lane's series bars and, on each entry in a bar, its series key (item 5)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from tests.http.test_understand_routes_timeline import Case
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.specialty)
async def test_lanes_carry_their_series_and_entries_their_series_key(
    combo: Combo, client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, combo)
    lane = next(lane for lane in case.pack.lanes() if lane.get("series"))
    rule: dict[str, Any] = lane["series"]
    fields: dict[str, Any] = {name: "zz" for name in rule["same"]}
    start = date(2024, 3, 1)
    ids = [case.fact(rule["kinds"][0], start + timedelta(days=20 * i), page=i + 1, **fields) for i in range(3)]
    lone = case.fact(rule["kinds"][0], start, page=9, **dict[str, Any]({name: "other" for name in rule["same"]}))
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")
    assert resp.status_code == 200
    body = {item["key"]: item for item in resp.json()["lanes"]}[lane["key"]]
    assert len(body["series"]) == 1
    bar = body["series"][0]
    assert bar["count"] == 3 and bar["fact_ids"] == [str(i) for i in ids]
    assert (bar["first"], bar["last"]) == ("2024-03-01", "2024-04-10")
    keys = {e["fact_id"]: e["series_key"] for e in body["events"]}
    assert {keys[str(i)] for i in ids} == {bar["key"]}
    assert keys[str(lone)] is None
