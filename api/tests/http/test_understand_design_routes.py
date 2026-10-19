"""What the S11, S12 and S13 mock-ups need from the server (design pass).

- Timeline: each event has a short summary (the words copied from the page) and its record source; the
  response lists the case's source types (key, label, colour: the pack's) and the numbers for the filter
  chips (checked, please check, things to look at, not linked), counted before any filter.
- Consultation: the entry says which questions it is linked to, its source and whether it has a thing to look at.
- Findings: each lists its evidence entries (kind, date, page, summary) for the one-line evidence summary.
- `POST /v1/me/checks/custom`: the expert adds a check of their own ("nothing followed this entry"); it appears
  with the case's checks, and kinds must exist in the case's pack.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.http.test_understand_routes_findings import Case as FindingCase
from tests.http.test_understand_routes_timeline import Case, _plain_lanes
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_the_timeline_lists_the_pack_s_sources_and_the_numbers_for_its_chips(
    combo: Combo, client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, combo)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    own = case.fact(kind, date(2024, 1, 10), status="verified")
    case.fact(kind, date(2024, 2, 10), status="suggested")  # hidden: page not read
    case.fact(kind, date(2024, 3, 10), status="edited")
    question = case.question()
    case.link(own, question)
    case.admin.execute("update app.pages set source_type = %s where case_id = %s", (case.pack.source_types()[0]["key"], case.id))
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")
    body = resp.json()
    assert [s["key"] for s in body["sources"]] == [s["key"] for s in case.pack.source_types()]
    assert [s["colour"] for s in body["sources"]] == [s["colour"] for s in case.pack.source_types()]
    vocab = case.pack.vocabulary.get("source_types", {})
    assert [s["label"] for s in body["sources"]] == [vocab.get(s["key"], s["key"]) for s in case.pack.source_types()]
    assert body["counts"] == {"checked": 2, "please_check": 0, "things_to_look": 0, "not_linked": 1, "total": 2}
    first = next(e for lane in body["lanes"] for e in lane["events"] if e["fact_id"] == str(own))
    assert first["summary"] == "zz"
    assert first["source"] == case.pack.source_types()[0]["key"]


async def test_the_chip_numbers_ignore_the_filters(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    case.fact(kind, date(2024, 1, 10))
    case.fact(kind, date(2024, 5, 10))
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline", params={"from": "2024-04-01"})
    body = resp.json()
    assert sum(len(lane["events"]) for lane in body["lanes"]) == 1
    assert body["counts"]["total"] == 2


async def test_a_consultation_entry_names_its_questions_source_and_things_to_look_at(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    visit = case.fact(kind, date(2024, 5, 15))
    earlier = case.fact(kind, date(2024, 5, 1), available=date(2024, 5, 2))
    q1, q2 = case.question(1), case.question(2)
    case.link(visit, q2)
    case.link(visit, q1)
    case.admin.execute(
        "insert into app.findings (case_id, check_key, rule, gentle, evidence_fact_ids, evidence_page_ids, pack_version)"
        " select %s, 'zz', 'zz', false, %s, array[page_id], 'zz@1' from app.facts where id = %s",
        (case.id, [earlier], earlier),
    )
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/consultations/{visit}")
    body = resp.json()
    assert body["question_numbers"] == [1, 2]
    assert body["entry"]["summary"] == "zz"
    seen = {e["fact_id"]: e for e in body["visible"]}
    assert seen[str(earlier)]["has_finding"] is True and body["entry"]["has_finding"] is False


async def test_findings_list_their_evidence_entries(client_for: ClientFor, seed: Seed) -> None:
    case = FindingCase(seed)
    finding = case.finding()
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/findings")
    [got] = [f for f in resp.json()["findings"] if f["id"] == str(finding)]
    [evidence] = got["evidence"]
    assert evidence["kind"] == "zz" and evidence["page_no"] == case.page_no
    assert evidence["event_date"] == "2024-02-14" and evidence["summary"] == "9"


def _own(case: FindingCase, **more: Any) -> dict[str, Any]:
    kinds = [k["key"] for k in case.pack.fact_kinds()]
    body = {"case_id": str(case.id), "label": "Nothing followed this", "applies_to": kinds[0], "followup_kinds": kinds[:1],
            "window_days": 21}  # fmt: skip
    return {**body, **more}


async def test_an_expert_adds_a_check_of_their_own_and_it_shows_with_the_case_s_checks(
    client_for: ClientFor, seed: Seed
) -> None:
    case = FindingCase(seed)
    expert = await client_for(case.expert)
    resp = await expert.post("/v1/me/checks/custom", json=_own(case))
    assert resp.status_code == 201, resp.text
    made = resp.json()
    assert made["custom"] is True and made["label"] == "Nothing followed this" and made["enabled"] is True
    listed = (await expert.get("/v1/me/checks", params={"case_id": str(case.id)})).json()["checks"]
    assert [c["key"] for c in listed][-1] == made["key"]
    assert [c["key"] for c in listed if not c.get("custom")] == [c["key"] for c in case.pack.checks()]
    mine = await (await client_for(case.assistant)).get("/v1/me/checks", params={"case_id": str(case.id)})
    assert all(not c["custom"] for c in mine.json()["checks"]), "your own checks are yours"
    off = await expert.patch("/v1/me/checks", json={"check_key": made["key"], "enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False


async def test_an_own_check_must_use_kinds_the_pack_has_and_needs_a_case_you_can_see(
    client_for: ClientFor, seed: Seed
) -> None:
    case = FindingCase(seed)
    expert = await client_for(case.expert)
    assert (await expert.post("/v1/me/checks/custom", json=_own(case, applies_to="nonsense"))).status_code == 422
    assert (await expert.post("/v1/me/checks/custom", json=_own(case, followup_kinds=[]))).status_code == 422
    assert (await expert.post("/v1/me/checks/custom", json=_own(case, label=" "))).status_code == 422
    assert (await expert.post("/v1/me/checks/custom", json=_own(case, window_days=0))).status_code == 422
    stranger = await client_for(case.stranger)
    assert (await stranger.post("/v1/me/checks/custom", json=_own(case))).status_code == 404
