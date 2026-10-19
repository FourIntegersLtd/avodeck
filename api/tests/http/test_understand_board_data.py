"""What the S11, S12 and S13 boards still needed from the server (board-data pass).

- Timeline lanes carry the pack's icon name and a subtitle counting their entries ("4 visits and calls").
- A consultation carries the clinician's name and the kind of visit ("Dr P · face to face"), the pack's
  card titles (with its clinician word) and where on the page the entry and the later entries are.
- Findings carry the pack's title and icon for their check.
Expected words are read from the pack, never written here (rule 11).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from tests.http.test_understand_routes_timeline import Case, _plain_lanes
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def _subtitle(case: Case, key: str, count: int) -> str:
    form = "lane_subtitle_one" if count == 1 else "lane_subtitle_other"
    return str(case.pack.vocabulary[form][key]).replace("{count}", str(count))


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_a_lane_carries_the_pack_s_icon_and_a_subtitle_counting_its_entries(
    combo: Combo, client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, combo)
    lane, other = _plain_lanes(case.pack)[:2]
    for day in (1, 2, 3):
        case.fact(lane["kinds"][0], date(2024, 5, day))
    case.fact(other["kinds"][0], date(2024, 5, 1))
    body = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")).json()
    got = {x["key"]: x for x in body["lanes"]}
    assert got[lane["key"]]["icon"] == lane["icon"]
    assert got[lane["key"]]["subtitle"] == _subtitle(case, lane["key"], 3)
    assert got[other["key"]]["subtitle"] == _subtitle(case, other["key"], 1)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_an_empty_lane_has_no_subtitle(combo: Combo, client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed, combo)
    body = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")).json()
    assert all(x["subtitle"] is None for x in body["lanes"])


async def test_a_consultation_carries_its_author_line_and_the_pack_s_card_titles(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, BOTH[0])
    kind = next(k for k in case.pack.fact_kinds() if k.get("author_field"))
    visit = case.fact(kind["key"], date(2024, 5, 15), **{kind["author_field"]: "Dr Q"})
    body = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/consultations/{visit}")).json()
    mode = case.pack.vocabulary["visit_modes"][kind["key"]]
    assert body["entry"]["author_line"] == f"Dr Q · {mode}"
    assert body["seen_title"] == case.pack.term("seen_title")
    assert body["unseen_title"] == case.pack.term("unseen_title")
    clinician = case.pack.term("clinician")
    assert clinician in body["seen_title"] and clinician in body["unseen_title"]


async def test_without_pack_words_a_consultation_has_no_author_line_or_card_titles(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, BOTH[1])
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    visit = case.fact(kind, date(2024, 5, 15))
    body = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/consultations/{visit}")).json()
    assert body["entry"]["author_line"] is None
    assert body["seen_title"] is None and body["unseen_title"] is None


async def test_the_page_marks_show_the_entry_and_blur_the_later_entries_on_the_same_page(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    visit = case.fact(kind, date(2024, 5, 15), page=3)
    earlier = case.fact(kind, date(2024, 5, 3), page=3, available=date(2024, 5, 3))
    later = case.fact(kind, date(2024, 6, 3), page=3, available=date(2024, 6, 4))
    elsewhere = case.fact(kind, date(2024, 6, 9), page=4, available=date(2024, 6, 10))
    nobox = case.fact(kind, date(2024, 6, 12), page=3, available=date(2024, 6, 13))
    boxes = {
        visit: (0.1, 0.4, 0.9, 0.5),
        earlier: (0.1, 0.1, 0.9, 0.2),
        later: (0.1, 0.6, 0.9, 0.7),
        elsewhere: (0.1, 0.1, 0.9, 0.2),
    }
    for fact, (x0, y0, x1, y1) in boxes.items():
        case.admin.execute(
            "update app.facts set bbox = %s where id = %s", (Jsonb({"x0": x0, "y0": y0, "x1": x1, "y1": y1}), fact)
        )
    body = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/consultations/{visit}")).json()
    marks = {m["fact_id"]: m for m in body["page_marks"]}
    assert set(marks) == {str(visit), str(later)}, "only this page; nothing without a box; not the ones seen"
    assert marks[str(visit)]["role"] == "entry" and marks[str(later)]["role"] == "later"
    assert marks[str(visit)]["box"] == {"x0": 0.1, "y0": 0.4, "x1": 0.9, "y1": 0.5}
    assert str(nobox) not in marks and str(elsewhere) not in marks


async def test_a_later_entry_the_person_may_not_see_yet_is_not_marked(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    visit = case.fact(kind, date(2024, 5, 15))
    hidden = case.fact(kind, date(2024, 6, 3), status="suggested", available=date(2024, 6, 4))
    case.admin.execute("update app.facts set bbox = %s", (Jsonb({"x0": 0, "y0": 0, "x1": 1, "y1": 1}),))
    body = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/consultations/{visit}")).json()
    assert str(hidden) not in {m["fact_id"] for m in body["page_marks"]}


def _finding(case: Case, check_key: str, rule: str) -> None:
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    fact = case.fact(kind, date(2024, 2, 14))
    case.admin.execute(
        "insert into app.findings (case_id, check_key, rule, gentle, evidence_fact_ids, evidence_page_ids,"
        " pack_version) select %s, %s, %s, false, %s, array[page_id], 'zz@1' from app.facts where id = %s",
        (case.id, check_key, rule, [fact], fact),
    )


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_a_finding_carries_the_pack_s_title_and_icon_for_its_check(
    combo: Combo, client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, combo)
    check = case.pack.checks()[0]
    _finding(case, str(check["key"]), str(check["rule"]))
    _finding(case, "own_abc123", "not_followed_up")
    got = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/findings")).json()["findings"]
    by_key: dict[str, Any] = {f["check_key"]: f for f in got}
    assert by_key[str(check["key"])]["title"] == case.pack.vocabulary["finding_titles"][str(check["key"])]
    assert by_key[str(check["key"])]["icon"] == check["icon"]
    assert by_key["own_abc123"]["title"] is None and by_key["own_abc123"]["icon"] is None


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_a_pack_check_is_listed_with_its_title_and_icon(combo: Combo, client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed, combo)
    resp = await (await client_for(case.expert)).get("/v1/me/checks", params={"case_id": str(case.id)})
    listed = resp.json()["checks"]
    for check, got in zip(case.pack.checks(), listed, strict=False):
        assert got["icon"] == check["icon"]
        assert got["title"] == case.pack.vocabulary["finding_titles"][str(check["key"])]
