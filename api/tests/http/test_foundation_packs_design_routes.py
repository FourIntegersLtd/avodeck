"""GET /v1/cases/{case_id}/pack grows: `subject_statuses`, `document_kinds`, `outside_expertise_parts`, and
`parts` and `colour` on every question type. Everything is pack data, under both combinations."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def case_pack(client_for: ClientFor, seed: Seed, combo: Combo) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, packs=combo.case_row())
    resp = await (await client_for(expert)).get(f"/v1/cases/{case}/pack")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_the_case_pack_carries_the_setup_screens_fields(client_for: ClientFor, seed: Seed, combo: Combo) -> None:
    body = await case_pack(client_for, seed, combo)
    vocab = combo.raw("jurisdiction", "vocabulary.yaml")
    case_type = combo.raw("case-type", "pack.yaml")

    assert [s["key"] for s in body["subject_statuses"]] == case_type["subject_statuses"]
    assert all(s["label"] == vocab["subject_statuses"][s["key"]] for s in body["subject_statuses"])
    assert {s["key"]: s["representative"] for s in body["subject_statuses"]} == {
        k: vocab["subject_representatives"].get(k) for k in case_type["subject_statuses"]
    }

    assert [k["key"] for k in body["document_kinds"]] == ["instructions", "records", "other", *case_type["document_kinds"]]

    raw_types = {q["key"]: q for q in combo.raw("case-type", "questions.yaml")["question_types"]}
    for q in body["question_types"]:
        assert q["colour"] == raw_types[q["key"]]["colour"]
        assert q["parts"] == raw_types[q["key"]]["parts"]

    assert body["outside_expertise_parts"] == combo.raw("specialty", "pack.yaml")["outside_expertise_parts"]
    assert body["vocabulary"]["other_side_prompt"] == vocab["other_side_prompt"]
    assert body["compliance_checklist"]["page_limit"] == combo.raw("jurisdiction", "report.yaml")["page_limit"]
