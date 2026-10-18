"""GET /v1/jurisdictions, /v1/jurisdictions/{code}, /v1/packs and /v1/cases/{case_id}/pack (architecture 5.2).

Test apps load the launch and the fixture packs; a cell lists only the jurisdictions whose `cell` is
its own. Every case-pack check runs under both combinations and compares against the pack files.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests.support.db import ClientFor
from tests.support.packs import BOTH, FIXTURE, LAUNCH, Combo
from tests.support.seed import Seed

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.core.db import Database

pytestmark = pytest.mark.anyio


@pytest.fixture
def cell_region() -> str:
    return str(FIXTURE.raw("jurisdiction", "pack.yaml")["cell"])


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], cell_region: str) -> FastAPI:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(
        database=api_db, region=cell_region, auth=SupabaseAuth(auth_settings(auth_env)), mailer=OutboxMailer()
    )


UK = str(LAUNCH.raw("jurisdiction", "pack.yaml")["cell"])


# --- jurisdictions ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("cell_region", [UK])
async def test_the_uk_cell_lists_only_its_own_jurisdiction(client_for: ClientFor, seed: Seed) -> None:
    resp = await (await client_for(seed.person())).get("/v1/jurisdictions")
    assert resp.status_code == 200, resp.text
    meta = LAUNCH.raw("jurisdiction", "pack.yaml")
    assert resp.json() == [{"code": LAUNCH.jurisdiction, "name": meta["name"], "version": meta["version"]}]


async def test_the_test_cell_lists_only_the_fixture_jurisdiction(client_for: ClientFor, seed: Seed) -> None:
    resp = await (await client_for(seed.person())).get("/v1/jurisdictions")
    assert [j["code"] for j in resp.json()] == [FIXTURE.jurisdiction]


@pytest.mark.parametrize("cell_region", [UK])
async def test_a_jurisdiction_summary_carries_vocabulary_declarations_checklist_and_deadline_rules(
    client_for: ClientFor, seed: Seed
) -> None:
    resp = await (await client_for(seed.person())).get(f"/v1/jurisdictions/{LAUNCH.jurisdiction}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    meta = LAUNCH.raw("jurisdiction", "pack.yaml")
    assert body["code"] == LAUNCH.jurisdiction
    assert body["version"] == meta["version"]
    assert (body["timezone"], body["date_order"], body["locale"]) == (
        meta["timezone"],
        meta["date_order"],
        meta["locale"],
    )
    assert body["vocabulary"] == LAUNCH.raw("jurisdiction", "vocabulary.yaml")
    [declaration] = body["declarations"]
    assert declaration["text"] in LAUNCH.raw("jurisdiction", "declarations.md")
    report = LAUNCH.raw("jurisdiction", "report.yaml")
    assert body["compliance_checklist"]["rule_ref"] == report["rule_ref"]
    assert [i["key"] for i in body["compliance_checklist"]["items"]] == [i["key"] for i in report["required_contents"]]
    rules = {r["key"]: r for r in body["deadline_rules"]}
    assert rules["written_questions"]["days"] == 28
    assert rules["written_questions"]["calendar"] == "calendar_days"
    assert body["legal_tests"]  # reference text, placeholders left for the case's specialty
    assert body["court_tracks"] == meta["court_tracks"]


@pytest.mark.parametrize("cell_region", [UK])
async def test_a_jurisdiction_from_another_cell_is_not_found(client_for: ClientFor, seed: Seed) -> None:
    resp = await (await client_for(seed.person())).get(f"/v1/jurisdictions/{FIXTURE.jurisdiction}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_the_fixture_jurisdiction_summary_uses_its_own_words(client_for: ClientFor, seed: Seed) -> None:
    resp = await (await client_for(seed.person())).get(f"/v1/jurisdictions/{FIXTURE.jurisdiction}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vocabulary"] == FIXTURE.raw("jurisdiction", "vocabulary.yaml")
    assert body["legal_tests"] is None
    assert body["court_tracks"] == []
    rules = {r["key"]: r for r in body["deadline_rules"]}
    assert (rules["written_questions"]["days"], rules["written_questions"]["calendar"]) == (21, "business_days")


async def test_jurisdictions_need_a_signed_in_person(client_for: ClientFor) -> None:
    resp = await (await client_for(None)).get("/v1/jurisdictions")
    assert resp.status_code == 401


# --- pack combinations offered in the cell ------------------------------------------------------------------------


@pytest.mark.parametrize("cell_region", [UK])
async def test_the_uk_cell_offers_the_launch_combination(client_for: ClientFor, seed: Seed) -> None:
    resp = await (await client_for(seed.person())).get("/v1/packs")
    assert resp.status_code == 200, resp.text
    combos = [(c["jurisdiction"]["code"], c["case_type"]["code"], c["specialty"]["code"]) for c in resp.json()]
    assert combos == [LAUNCH.codes]
    [combo] = resp.json()
    assert combo["specialty"]["name"] == LAUNCH.raw("specialty", "pack.yaml")["name"]
    assert combo["expert_roles"] == LAUNCH.raw("case-type", "pack.yaml")["expert_roles"]


async def test_the_test_cell_offers_the_fixture_combination(client_for: ClientFor, seed: Seed) -> None:
    resp = await (await client_for(seed.person())).get("/v1/packs")
    combos = {(c["jurisdiction"]["code"], c["case_type"]["code"], c["specialty"]["code"]) for c in resp.json()}
    assert FIXTURE.codes in combos
    assert all(j == FIXTURE.jurisdiction for j, _, _ in combos)


# --- the merged pack for a case, under both combinations ----------------------------------------------------------


async def case_pack(client_for: ClientFor, seed: Seed, combo: Combo) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, packs=combo.case_row())
    resp = await (await client_for(expert)).get(f"/v1/cases/{case}/pack")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_the_case_pack_follows_the_cases_packs(client_for: ClientFor, seed: Seed, combo: Combo) -> None:
    body = await case_pack(client_for, seed, combo)
    jurisdiction, case_type = combo.raw("jurisdiction", "pack.yaml"), combo.raw("case-type", "pack.yaml")
    vocab = combo.raw("jurisdiction", "vocabulary.yaml")
    assert body["versions"]["jurisdiction"] == f"{combo.jurisdiction}@{jurisdiction['version']}"
    assert (body["timezone"], body["date_order"]) == (jurisdiction["timezone"], jurisdiction["date_order"])
    assert body["party_roles"] == [{"key": k, "label": vocab["party_roles"][k]} for k in case_type["party_roles"]]
    assert body["vocabulary"]["clinician"] == combo.word("clinician")
    assert body["vocabulary"]["subject"] == vocab["subject"]
    assert [s["key"] for s in body["workflow_steps"]] == [
        s["key"] for s in combo.raw("case-type", "workflow.yaml")["steps"]
    ]
    raw_lanes = combo.raw("specialty", "lanes.yaml")["lanes"]
    assert [lane["key"] for lane in body["lanes"]] == [
        lane if isinstance(lane, str) else lane["key"] for lane in raw_lanes
    ]
    assert [q["key"] for q in body["question_types"]] == [
        q["key"] for q in combo.raw("case-type", "questions.yaml")["question_types"]
    ]
    assert [f["key"] for f in body["fact_kinds"]] == [
        f["key"] for f in combo.raw("specialty", "facts.yaml")["fact_kinds"]
    ]
    [declaration] = body["declarations"]
    key = re.search(r"^key:\s*(\S+)$", combo.raw("jurisdiction", "declarations.md"), re.M)
    assert key is not None and declaration["key"] == key.group(1)
    assert {r["key"] for r in body["deadline_rules"]} >= {"written_questions"}


async def test_the_case_pack_differs_between_the_launch_and_fixture_packs(client_for: ClientFor, seed: Seed) -> None:
    launch, fixture = await case_pack(client_for, seed, LAUNCH), await case_pack(client_for, seed, FIXTURE)
    for field in (
        "versions",
        "timezone",
        "date_order",
        "party_roles",
        "workflow_steps",
        "lanes",
        "question_types",
        "fact_kinds",
        "declarations",
        "deadline_rules",
    ):
        assert launch[field] != fixture[field], field
    for word in ("clinician", "subject", "court_bundle", "written_questions"):
        assert launch["vocabulary"][word] != fixture["vocabulary"][word], word


async def test_the_case_pack_is_not_found_for_a_person_without_access(client_for: ClientFor, seed: Seed) -> None:
    expert, outsider = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    resp = await (await client_for(outsider)).get(f"/v1/cases/{case}/pack")
    assert resp.status_code == 404
    resp = await (await client_for(expert)).get(f"/v1/cases/{uuid.uuid4()}/pack")
    assert resp.status_code == 404
