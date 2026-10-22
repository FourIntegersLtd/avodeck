"""`GET /timeline/overview` (the ribbon): the whole record counted per lane per month, plus pins."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest

from tests.http.test_understand_routes_timeline import Case, _plain_lanes
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def _overview(client_for: ClientFor, case: Case, **params: str) -> dict[str, Any]:
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline/overview", params=params)
    assert resp.status_code == 200
    return dict(resp.json())


def _flag(case: Case, *facts: uuid.UUID) -> None:
    case.admin.execute(
        "insert into app.findings (case_id, check_key, rule, evidence_fact_ids, pack_version)"
        " values (%s, 'zz_check', 'zz_rule', %s, 'zz')",
        (case.id, list(facts)),
    )


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_counts_per_lane_per_month_in_the_pack_s_lane_order(
    combo: Combo, client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, combo)
    lanes = _plain_lanes(case.pack)
    first, second = lanes[0], lanes[1]
    case.fact(first["kinds"][0], date(2024, 1, 10))
    case.fact(first["kinds"][0], date(2024, 1, 20))
    case.fact(second["kinds"][0], date(2024, 3, 2))
    case.fact(second["kinds"][0], None)
    body = await _overview(client_for, case)
    assert body["range"] == {"first": "2024-01-10", "last": "2024-03-02"}
    assert body["months"] == ["2024-01", "2024-02", "2024-03"]
    assert [lane["key"] for lane in body["lanes"]] == [lane["key"] for lane in case.pack.lanes()]
    got = {lane["key"]: lane for lane in body["lanes"]}
    assert got[first["key"]]["counts"] == [2, 0, 0]
    assert got[second["key"]]["counts"] == [0, 0, 1]
    assert got[first["key"]]["label"] == case.pack.vocabulary["lanes"][first["key"]]
    assert all(len(lane["counts"]) == 3 for lane in body["lanes"])


async def test_months_span_the_year_end(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    case.fact(kind, date(2023, 11, 30))
    case.fact(kind, date(2024, 2, 1))
    assert (await _overview(client_for, case))["months"] == ["2023-11", "2023-12", "2024-01", "2024-02"]


async def test_the_lane_colour_is_its_commonest_source_with_a_colour(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    lane_key = _plain_lanes(case.pack)[0]["key"]
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    coloured = [s for s in case.pack.source_types() if s.get("colour")]
    assert coloured
    for _ in range(2):
        fact = case.fact(kind, date(2024, 1, 10))
        case.admin.execute("update app.facts set source = %s where id = %s", (coloured[0]["key"], fact))
    body = await _overview(client_for, case)
    lane = next(lane for lane in body["lanes"] if lane["key"] == lane_key)
    assert lane["colour"] == coloured[0]["colour"]


async def test_suggestions_on_unread_pages_are_not_counted(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    case.fact(kind, date(2024, 1, 10), status="suggested")
    body = await _overview(client_for, case)
    assert body["range"] is None and body["pins"] == []
    assert all(sum(lane["counts"]) == 0 for lane in body["lanes"])


async def test_as_seen_on_drops_what_arrived_later(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    case.fact(kind, date(2024, 1, 10), available=date(2024, 1, 12))
    case.fact(kind, date(2024, 2, 10), available=date(2024, 3, 1))
    case.fact(kind, date(2024, 2, 11))
    body = await _overview(client_for, case, as_seen_on="2024-02-01")
    assert body["range"] == {"first": "2024-01-10", "last": "2024-01-10"}
    assert body["months"] == ["2024-01"]
    assert sum(sum(lane["counts"]) for lane in body["lanes"]) == 1
    assert len((await _overview(client_for, case))["months"]) == 2


async def test_pins_mark_open_findings_and_linked_questions_only(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    lanes = _plain_lanes(case.pack)
    flagged = case.fact(lanes[0]["kinds"][0], date(2024, 3, 5))
    linked = case.fact(lanes[1]["kinds"][0], date(2024, 1, 5))
    case.fact(lanes[0]["kinds"][0], date(2024, 2, 5))
    question = case.question()
    case.link(linked, question)
    _flag(case, flagged)
    body = await _overview(client_for, case)
    assert body["pins"] == [
        {
            "fact_id": str(linked),
            "date": "2024-01-05",
            "lane_key": lanes[1]["key"],
            "has_finding": False,
            "question_ids": [str(question)],
        },
        {
            "fact_id": str(flagged),
            "date": "2024-03-05",
            "lane_key": lanes[0]["key"],
            "has_finding": True,
            "question_ids": [],
        },
    ]


async def test_an_empty_record_gives_no_range_and_no_months(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    body = await _overview(client_for, case)
    assert body["range"] is None and body["months"] == [] and body["pins"] == []
    assert [lane["counts"] for lane in body["lanes"]] == [[] for _ in case.pack.lanes()]


async def test_someone_outside_the_case_gets_404(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    resp = await (await client_for(case.stranger)).get(f"/v1/cases/{case.id}/timeline/overview")
    assert resp.status_code == 404
