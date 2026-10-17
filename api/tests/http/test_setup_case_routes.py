"""Case setup routes (architecture 5.2, slice C brief "Extra endpoints" and behaviour rules).

Members, parties, instructing parties (a joint expert has two; a change of firm keeps history),
instruction versions and dated oral instructions (typed or from a transcript), case links, the
duplicate-case check (a keyed hash; the identifier is never stored), the jurisdiction change (before
serving only, step-up, packs re-stamped, changes listed), hold and resume, and the subject status, which
changes words only. Every route is a case route and answers 404 to people outside the case (rule 9).
Everything runs under the fixture packs, so no launch word is assumed (rule 11).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.packs import PackLibrary, default_library, load_library
from tests.support.db import ClientFor
from tests.support.packs import FIXTURE, copy_packs
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

FIXTURE_CELL = str(FIXTURE.raw("jurisdiction", "pack.yaml")["cell"])
PARTY_ROLES = list(FIXTURE.raw("case-type", "pack.yaml")["party_roles"])
IP_TYPES = list(FIXTURE.raw("case-type", "pack.yaml")["instructing_party_types"])
VOCABULARY = FIXTURE.raw("jurisdiction", "vocabulary.yaml")


@pytest.fixture
def packs() -> PackLibrary:
    return default_library()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], packs: PackLibrary) -> FastAPI:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(
        database=api_db,
        region=FIXTURE_CELL,
        auth=SupabaseAuth(auth_settings(auth_env)),
        mailer=OutboxMailer(),
        packs=packs,
        crypto=CaseCrypto(LocalKeyProvider.generate()),
    )


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant, viewer, colleague, outsider = (seed.person() for _ in range(5))
    org = seed.org(
        kind="team", members={expert: "expert", assistant: "assistant", viewer: "expert", colleague: "expert"}
    )
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    return {
        "expert": expert,
        "assistant": assistant,
        "viewer": viewer,
        "colleague": colleague,
        "outsider": outsider,
        "org": org,
        "case": case,
    }


def url(world: dict[str, uuid.UUID], tail: str) -> str:
    return f"/v1/cases/{world['case']}/{tail}"


# --- members ---------------------------------------------------------------------------------------


async def test_members_are_listed_added_and_removed(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    client = await client_for(world["expert"])
    listed = (await client.get(url(world, "members"))).json()
    assert {(m["person_id"], m["case_role"]) for m in listed} == {
        (str(world["assistant"]), "assistant"),
        (str(world["viewer"]), "viewer"),
    }
    added = await client.post(url(world, "members"), json={"person_id": str(world["colleague"]), "case_role": "viewer"})
    assert added.status_code == 201, added.text
    again = await client.post(url(world, "members"), json={"person_id": str(world["colleague"]), "case_role": "viewer"})
    assert again.status_code == 409
    removed = await client.delete(url(world, f"members/{added.json()['id']}"))
    assert removed.status_code == 204
    assert str(world["colleague"]) not in {m["person_id"] for m in (await client.get(url(world, "members"))).json()}


async def test_only_people_of_the_organisation_join_and_only_the_named_expert_adds(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    expert = await client_for(world["expert"])
    body = {"person_id": str(world["outsider"]), "case_role": "viewer"}
    assert (await expert.post(url(world, "members"), json=body)).status_code == 422
    bad_role = {"person_id": str(world["colleague"]), "case_role": "named_expert"}
    assert (await expert.post(url(world, "members"), json=bad_role)).status_code == 422
    assistant = await client_for(world["assistant"])
    body = {"person_id": str(world["colleague"]), "case_role": "viewer"}
    assert (await assistant.post(url(world, "members"), json=body)).status_code == 403
    assert (await (await client_for(world["outsider"])).get(url(world, "members"))).status_code == 404


# --- parties ---------------------------------------------------------------------------------------


async def test_any_number_of_parties_with_pack_role_keys(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(world["assistant"])
    first = await client.post(url(world, "parties"), json={"role_key": PARTY_ROLES[1], "name": "Zedd Surgery"})
    second = await client.post(
        url(world, "parties"), json={"role_key": PARTY_ROLES[1], "name": "Zedd Hospital", "representative": "Q Firm"}
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201
    listed = (await client.get(url(world, "parties"))).json()
    assert [p["name"] for p in listed] == ["Zedd Surgery", "Zedd Hospital"]
    stored = admin.execute("select name, representative from app.case_parties where case_id = %s", (world["case"],))
    assert all(b"Zedd" not in bytes(r[0]) for r in stored.fetchall())

    party = first.json()
    changed = await client.patch(
        url(world, f"parties/{party['id']}"),
        json={"role_key": PARTY_ROLES[0]},
        headers={"if-match": first.headers["etag"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["role_key"] == PARTY_ROLES[0]
    stale = await client.patch(
        url(world, f"parties/{party['id']}"), json={"name": "X"}, headers={"if-match": first.headers["etag"]}
    )
    assert stale.status_code == 412
    assert (await client.delete(url(world, f"parties/{party['id']}"))).status_code == 204
    assert len((await client.get(url(world, "parties"))).json()) == 1


async def test_a_party_role_must_come_from_the_packs(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    client = await client_for(world["expert"])
    assert (await client.post(url(world, "parties"), json={"role_key": "not_a_role", "name": "X"})).status_code == 422
    viewer = await client_for(world["viewer"])
    body = {"role_key": PARTY_ROLES[0], "name": "X"}
    assert (await viewer.post(url(world, "parties"), json=body)).status_code == 403
    assert (await (await client_for(world["outsider"])).get(url(world, "parties"))).status_code == 404


async def test_a_party_can_be_added_after_serving_but_not_changed_or_removed(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    served = seed.case(world["org"], world["expert"], status="served")
    seed.admin.execute(
        "insert into app.case_parties (id, case_id, role_key, name) values (%s, %s, %s, '\\x00')",
        (existing := uuid.uuid4(), served, PARTY_ROLES[0]),
    )
    client = await client_for(world["expert"])
    added = await client.post(f"/v1/cases/{served}/parties", json={"role_key": PARTY_ROLES[0], "name": "X"})
    assert added.status_code == 201, added.text
    changed = await client.patch(
        f"/v1/cases/{served}/parties/{existing}", json={"name": "Y"}, headers={"if-match": added.headers["etag"]}
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "conflict"
    removed = await client.delete(f"/v1/cases/{served}/parties/{existing}")
    assert removed.status_code == 409
    assert removed.json()["error"]["code"] == "conflict"


# --- instructing parties ---------------------------------------------------------------------------


async def test_a_joint_expert_has_two_instructing_parties(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    client = await client_for(world["expert"])
    for firm in ("North Firm", "South Firm"):
        body = {"type_key": IP_TYPES[0], "name": "A Person", "firm": firm, "email": "a@example.test"}
        assert (await client.post(url(world, "instructing-parties"), json=body)).status_code == 201
    listed = (await client.get(url(world, "instructing-parties"))).json()
    assert listed["joint"] is True
    assert sorted(p["firm"] for p in listed["current"]) == ["North Firm", "South Firm"]


async def test_a_change_of_firm_keeps_the_old_firm_with_dates(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["expert"])
    body = {"type_key": IP_TYPES[0], "name": "A Person", "firm": "Old Firm", "effective_from": "2024-01-10"}
    created = await client.post(url(world, "instructing-parties"), json=body)
    assert created.status_code == 201, created.text
    ip = created.json()
    same = await client.patch(
        url(world, f"instructing-parties/{ip['id']}"),
        json={"name": "B Person"},
        headers={"if-match": created.headers["etag"]},
    )
    assert same.status_code == 200, same.text
    assert same.json()["id"] == ip["id"]
    moved = await client.patch(
        url(world, f"instructing-parties/{ip['id']}"),
        json={"firm": "New Firm", "effective_from": "2024-06-01"},
        headers={"if-match": same.headers["etag"]},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["id"] != ip["id"]
    listed = (await client.get(url(world, "instructing-parties"))).json()
    assert [p["firm"] for p in listed["current"]] == ["New Firm"]
    assert listed["joint"] is False
    assert [(p["firm"], p["effective_from"], p["effective_to"]) for p in listed["history"]] == [
        ("Old Firm", "2024-01-10", "2024-05-31")
    ]
    old = await client.patch(
        url(world, f"instructing-parties/{ip['id']}"), json={"name": "C"}, headers={"if-match": moved.headers["etag"]}
    )
    assert old.status_code == 409


async def test_an_instructing_party_type_must_come_from_the_packs(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["expert"])
    body = {"type_key": "not_a_type", "name": "A", "firm": "F"}
    assert (await client.post(url(world, "instructing-parties"), json=body)).status_code == 422


# --- instructions ----------------------------------------------------------------------------------


async def test_a_supplementary_letter_creates_a_new_instruction_version(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(world["expert"])
    first = await client.post(url(world, "instructions"), json={"kind": "letter", "received_on": "2024-02-01"})
    assert first.status_code == 201, first.text
    assert first.json()["version_no"] == 1
    assert first.json()["reopens_questions"] is False
    admin.execute("update app.instruction_versions set locked_at = now() where id = %s", (first.json()["id"],))
    second = await client.post(url(world, "instructions"), json={"kind": "supplementary", "received_on": "2024-05-01"})
    assert second.status_code == 201
    assert second.json()["version_no"] == 2
    assert second.json()["reopens_questions"] is True
    listed = (await client.get(url(world, "instructions"))).json()
    assert [(v["version_no"], v["kind"], v["locked"]) for v in listed] == [
        (1, "letter", True),
        (2, "supplementary", False),
    ]


async def test_instruction_kinds_and_documents_are_checked(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["expert"])
    for kind in ("oral", "not_a_kind"):
        assert (await client.post(url(world, "instructions"), json={"kind": kind})).status_code == 422
    other = seed.case(world["org"], world["expert"], status="active")
    document = seed.document(other)
    body = {"kind": "letter", "document_id": str(document)}
    assert (await client.post(url(world, "instructions"), json=body)).status_code == 422
    viewer = await client_for(world["viewer"])
    assert (await viewer.post(url(world, "instructions"), json={"kind": "letter"})).status_code == 403


async def test_oral_instructions_save_as_a_dated_record(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(world["expert"])
    body = {"received_on": "2024-03-04", "text": "Please also look at the March visit."}
    saved = await client.put(url(world, "oral-instructions"), json=body)
    assert saved.status_code == 200, saved.text
    record = saved.json()
    assert (record["received_on"], record["text"], record["source"]) == ("2024-03-04", body["text"], "typed")
    again = await client.put(url(world, "oral-instructions"), json={**body, "text": "Only the March visit."})
    assert again.json()["instruction_version_id"] == record["instruction_version_id"]
    assert again.json()["text"] == "Only the March visit."
    listed = (await client.get(url(world, "instructions"))).json()
    assert [(v["kind"], v["received_on"], v["text"]) for v in listed] == [
        ("oral", "2024-03-04", "Only the March visit.")
    ]
    stored = admin.execute("select text from app.case_instructions where case_id = %s", (world["case"],)).fetchall()
    assert stored and all(b"March" not in bytes(r[0]) for r in stored)


async def test_oral_instructions_can_come_from_a_transcript(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    client = await client_for(world["expert"])
    transcript = await client.post(
        url(world, "transcripts"),
        json={"text": "Dictated words.", "context": "oral_instructions", "duration_seconds": 4},
    )
    assert transcript.status_code == 201, transcript.text
    body = {"received_on": "2024-03-05", "transcript_id": transcript.json()["id"]}
    saved = await client.put(url(world, "oral-instructions"), json=body)
    assert saved.status_code == 200, saved.text
    assert (saved.json()["text"], saved.json()["source"]) == ("Dictated words.", "dictated")
    both = {**body, "text": "x"}
    assert (await client.put(url(world, "oral-instructions"), json=both)).status_code == 422
    assert (await client.put(url(world, "oral-instructions"), json={"received_on": "2024-03-05"})).status_code == 422
    other = {"received_on": "2024-03-06", "transcript_id": str(uuid.uuid4())}
    assert (await client.put(url(world, "oral-instructions"), json=other)).status_code == 422


# --- links -----------------------------------------------------------------------------------------


async def test_cases_link_without_sharing_data(client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]) -> None:
    other = seed.case(world["org"], world["expert"], status="active")
    client = await client_for(world["expert"])
    created = await client.post(url(world, "links"), json={"linked_case_id": str(other), "kind": "related"})
    assert created.status_code == 201, created.text
    listed = (await client.get(url(world, "links"))).json()
    assert [(link["linked_case_id"], link["kind"]) for link in listed] == [(str(other), "related")]
    assert set(listed[0]) <= {"id", "linked_case_id", "kind", "label", "created_at"}
    assert (await client.delete(url(world, f"links/{created.json()['id']}"))).status_code == 204
    assert (await client.get(url(world, "links"))).json() == []


async def test_a_link_needs_a_case_you_can_see(client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]) -> None:
    org = seed.org(kind="personal", members={world["outsider"]: "owner"})
    hidden = seed.case(org, world["outsider"], status="active")
    client = await client_for(world["expert"])
    assert (
        await client.post(url(world, "links"), json={"linked_case_id": str(hidden), "kind": "related"})
    ).status_code == 404
    assert (
        await client.post(url(world, "links"), json={"linked_case_id": str(world["case"]), "kind": "related"})
    ).status_code == 422
    other = seed.case(world["org"], world["expert"], status="active")
    assert (
        await client.post(url(world, "links"), json={"linked_case_id": str(other), "kind": "nope"})
    ).status_code == 422


async def test_a_link_needs_access_to_both_cases_and_the_other_answers_not_found(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    # A colleague's case in the same organisation that the assistant is not a member of.
    colleagues = seed.case(world["org"], world["colleague"], status="active")
    assistant = await client_for(world["assistant"])
    body = {"linked_case_id": str(colleagues), "kind": "related"}
    refused = await assistant.post(url(world, "links"), json=body)
    assert refused.status_code == 404
    missing = await assistant.post(url(world, "links"), json={**body, "linked_case_id": str(uuid.uuid4())})
    assert missing.status_code == 404
    assert refused.json() == missing.json()


# --- duplicate check -------------------------------------------------------------------------------

IDENTIFIER = "ZZ-ID-123456"


async def test_the_duplicate_check_compares_a_keyed_hash_and_never_stores_the_identifier(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(world["expert"])
    saved = await client.put(url(world, "subject-key"), json={"identifier": IDENTIFIER.lower() + " "})
    assert saved.status_code == 204, saved.text
    rows = admin.execute("select key_hash from app.case_subject_keys where case_id = %s", (world["case"],)).fetchall()
    assert len(rows) == 1 and IDENTIFIER not in rows[0][0] and len(rows[0][0]) == 64
    dumped = admin.execute(
        "select count(*) from app.idempotency_keys where encode(response_body, 'escape') ilike %s", (f"%{IDENTIFIER}%",)
    )
    assert dumped.fetchone() == (0,)

    check = {"jurisdiction": FIXTURE.jurisdiction, "identifier": IDENTIFIER}
    found = await client.post("/v1/cases/duplicate-check", json=check)
    assert found.status_code == 200, found.text
    assert [m["case_id"] for m in found.json()["matches"]] == [str(world["case"])]
    other = await client.post("/v1/cases/duplicate-check", json={**check, "identifier": "ZZ-ID-654321"})
    assert other.json()["matches"] == []
    assert (await client.post("/v1/cases/duplicate-check", json={**check, "identifier": "12"})).status_code == 422
    assert (await client.put(url(world, "subject-key"), json={"identifier": "12"})).status_code == 422

    stranger = await client_for(world["outsider"])
    assert (await stranger.post("/v1/cases/duplicate-check", json=check)).json()["matches"] == []


async def test_the_duplicate_check_says_when_the_instructing_firm_is_the_same(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["expert"])
    await client.put(url(world, "subject-key"), json={"identifier": IDENTIFIER})
    body = {"type_key": IP_TYPES[0], "name": "A Person", "firm": "North Firm"}
    await client.post(url(world, "instructing-parties"), json=body)
    check = {"jurisdiction": FIXTURE.jurisdiction, "identifier": IDENTIFIER, "instructing_firm": "north firm"}
    match = (await client.post("/v1/cases/duplicate-check", json=check)).json()["matches"][0]
    assert match["same_instructing_party"] is True
    check["instructing_firm"] = "South Firm"
    assert (await client.post("/v1/cases/duplicate-check", json=check)).json()["matches"][0][
        "same_instructing_party"
    ] is False


# --- jurisdiction ----------------------------------------------------------------------------------

ALT = "zz-alt"


def library_with_second_jurisdiction(tmp_path: Path) -> PackLibrary:
    launch_root, fixture_root = copy_packs(tmp_path)
    source = fixture_root / "jurisdiction" / FIXTURE.jurisdiction
    target = fixture_root / "jurisdiction" / ALT
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
    meta = yaml.safe_load((target / "pack.yaml").read_text())
    meta.update(code=ALT, name="Otherland", version="0.0.9")
    (target / "pack.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
    vocabulary = yaml.safe_load((target / "vocabulary.yaml").read_text())
    vocabulary["subject"] = "applicant"
    (target / "vocabulary.yaml").write_text(yaml.safe_dump(vocabulary, sort_keys=False))
    declaration = (target / "declarations.md").read_text()
    (target / "declarations.md").write_text(declaration.replace("this test report", "this other report"))
    return load_library(launch_root, fixture_root)


async def test_the_jurisdiction_changes_before_serving_with_packs_restamped(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection, app: FastAPI, tmp_path: Path
) -> None:
    app.state.packs = library_with_second_jurisdiction(tmp_path)
    client = await client_for(world["expert"])
    tag = (await client.get(f"/v1/cases/{world['case']}")).headers["etag"]
    body = {"jurisdiction": ALT, "case_type": FIXTURE.case_type, "specialty": FIXTURE.specialty}
    resp = await client.patch(url(world, "jurisdiction"), json=body, headers={"if-match": tag})
    assert resp.status_code == 200, resp.text
    changes = resp.json()["changes"]
    assert {"what": "pack", "key": "jurisdiction", "before": FIXTURE.jurisdiction, "after": ALT} in changes
    assert {"what": "word", "key": "subject", "before": VOCABULARY["subject"], "after": "applicant"} in changes
    assert any(c["what"] == "declaration" for c in changes)
    row = admin.execute("select jurisdiction, pack_versions from app.cases where id = %s", (world["case"],)).fetchone()
    assert row is not None
    assert row[0] == ALT
    assert row[1] == app.state.packs.combine(ALT, FIXTURE.case_type, FIXTURE.specialty).versions
    assert (await client.patch(url(world, "jurisdiction"), json=body, headers={"if-match": tag})).status_code == 412


async def test_the_jurisdiction_change_is_refused_when_it_should_be(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID], app: FastAPI, tmp_path: Path
) -> None:
    app.state.packs = library_with_second_jurisdiction(tmp_path)
    body = {"jurisdiction": ALT, "case_type": FIXTURE.case_type, "specialty": FIXTURE.specialty}
    served = seed.case(world["org"], world["expert"], status="served")
    client = await client_for(world["expert"])
    tag = (await client.get(f"/v1/cases/{served}")).headers["etag"]
    refused = await client.patch(f"/v1/cases/{served}/jurisdiction", json=body, headers={"if-match": tag})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "conflict"

    tag = (await client.get(f"/v1/cases/{world['case']}")).headers["etag"]
    unknown = {**body, "jurisdiction": "zz-nowhere"}
    assert (await client.patch(url(world, "jurisdiction"), json=unknown, headers={"if-match": tag})).status_code == 422
    stale_mfa = await client_for(world["expert"], mfa_age=timedelta(minutes=20))
    step_up = await stale_mfa.patch(url(world, "jurisdiction"), json=body, headers={"if-match": tag})
    assert step_up.status_code == 403
    assert step_up.json()["error"]["code"] == "step_up_required"
    assistant = await client_for(world["assistant"])
    assert (await assistant.patch(url(world, "jurisdiction"), json=body, headers={"if-match": tag})).status_code == 403


# --- hold and resume -------------------------------------------------------------------------------


async def test_a_case_goes_on_hold_and_resumes(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    assistant = await client_for(world["assistant"])
    held = await assistant.post(url(world, "hold"))
    assert held.status_code == 200, held.text
    assert held.json()["on_hold"] is True
    assert (await assistant.get(f"/v1/cases/{world['case']}")).json()["on_hold"] is True
    viewer = await client_for(world["viewer"])
    assert (await viewer.post(url(world, "resume"))).status_code == 403
    resumed = await assistant.post(url(world, "resume"))
    assert resumed.json()["on_hold"] is False
    assert (await (await client_for(world["outsider"])).post(url(world, "hold"))).status_code == 404


# --- subject status and who the case is for ---------------------------------------------------------


async def test_the_subject_status_changes_only_the_words(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    client = await client_for(world["expert"])
    before_pack = (await client.get(url(world, "pack"))).json()
    before_case = await client.get(f"/v1/cases/{world['case']}")
    status = list(VOCABULARY["subject_representatives"])[0]
    changed = await client.patch(
        f"/v1/cases/{world['case']}", json={"subject_status": status}, headers={"if-match": before_case.headers["etag"]}
    )
    assert changed.status_code == 200, changed.text
    subject = (await client.get(url(world, "subject"))).json()
    assert subject == {
        "status": status,
        "label": VOCABULARY["subject_statuses"][status],
        "representative": VOCABULARY["subject_representatives"][status],
    }
    assert (await client.get(url(world, "pack"))).json() == before_pack
    keep = ("jurisdiction", "case_type", "specialty", "expert_role", "status", "party_role")
    after: dict[str, Any] = changed.json()
    assert {k: after[k] for k in keep} == {k: before_case.json()[k] for k in keep}


async def test_an_assistant_creates_a_case_for_an_expert(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    client = await client_for(world["assistant"])
    body = {
        "organisation_id": str(world["org"]),
        "jurisdiction": FIXTURE.jurisdiction,
        "case_type": FIXTURE.case_type,
        "specialty": FIXTURE.specialty,
        "label": "Case Y",
        "named_expert_id": str(world["colleague"]),
    }
    created = await client.post("/v1/cases", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["named_expert_id"] == str(world["colleague"])
    members = (await client.get(f"/v1/cases/{created.json()['id']}/members")).json()
    assert [(m["person_id"], m["case_role"]) for m in members] == [(str(world["assistant"]), "assistant")]
