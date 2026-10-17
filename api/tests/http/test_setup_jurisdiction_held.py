"""Changing the packs before serving: keys held outside the case row (slice C review finding 2).

Party roles, instructing party types, question kinds and conflict question keys are stored on other
tables. When the new packs do not have one, the change is not refused (the expert could never fix the
key first, since every key is checked against the case's current packs): the row is kept as it is and
listed in `changes` as `held`, for the expert to change on the screens. Suggestions from the letter
still waiting for the expert were checked against the old packs' words, so they are dismissed and
listed as `dismissed`. Everything runs under the fixture packs (rule 11).
"""

# ruff: noqa: F811  (the shared fixtures are imported from the case tests)
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import psycopg
import pytest
import yaml
from fastapi import FastAPI

from app.core.packs import PackLibrary, load_library
from tests.http.test_setup_case_routes import ALT, app, packs, url, world  # noqa: F401
from tests.support.db import ClientFor
from tests.support.packs import FIXTURE, copy_packs

pytestmark = pytest.mark.anyio

ALT_CASE = "zz-case-alt"


def library_with_other_keys(tmp_path: Path) -> PackLibrary:
    """A second jurisdiction without the second conflict question, and a second case type with other keys."""
    launch_root, fixture_root = copy_packs(tmp_path)
    j_target = fixture_root / "jurisdiction" / ALT
    shutil.copytree(fixture_root / "jurisdiction" / FIXTURE.jurisdiction, j_target, ignore=shutil.ignore_patterns("__pycache__"))
    meta = yaml.safe_load((j_target / "pack.yaml").read_text())
    meta.update(code=ALT, name="Otherland", version="0.0.9")
    (j_target / "pack.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
    conflicts = yaml.safe_load((j_target / "conflicts.yaml").read_text())
    conflicts["questions"] = conflicts["questions"][:1]
    (j_target / "conflicts.yaml").write_text(yaml.safe_dump(conflicts, sort_keys=False))

    ct_target = fixture_root / "case-type" / ALT_CASE
    shutil.copytree(fixture_root / "case-type" / FIXTURE.case_type, ct_target)
    ct = yaml.safe_load((ct_target / "pack.yaml").read_text())
    ct.update(code=ALT_CASE, version="0.0.9", party_roles=ct["party_roles"][1:], instructing_party_types=["lawyer"])
    (ct_target / "pack.yaml").write_text(yaml.safe_dump(ct, sort_keys=False))
    questions = yaml.safe_load((ct_target / "questions.yaml").read_text())
    questions["question_types"] = questions["question_types"][:1]
    (ct_target / "questions.yaml").write_text(yaml.safe_dump(questions, sort_keys=False))
    return load_library(launch_root, fixture_root)


def seed_keys(admin: psycopg.Connection, case: uuid.UUID, old: PackLibrary) -> dict[str, str]:
    pack = old.combine(*FIXTURE.codes)
    role = pack.party_roles()[0]["key"]
    ip_type = pack.instructing_party_types()[0]["key"]
    kind = pack.question_types()[-1]["key"]
    conflict_key = pack.conflicts()["questions"][-1]["key"]
    admin.execute("insert into app.case_parties (case_id, role_key, name) values (%s, %s, %s)", (case, role, b"x"))
    admin.execute(
        "insert into app.case_instructing_parties (case_id, type_key, name, firm) values (%s, %s, 'A', 'B')",
        (case, ip_type),
    )
    version = admin.execute(
        "insert into app.instruction_versions (case_id, version_no, kind) values (%s, 1, 'letter') returning id",
        (case,),
    ).fetchone()
    assert version is not None
    admin.execute(
        "insert into app.questions (case_id, instruction_version_id, number, text_verbatim, kind)"
        " values (%s, %s, 1, 'Q', %s)",
        (case, version[0], kind),
    )
    admin.execute(
        "insert into app.conflict_answers (case_id, question_key, answer) values (%s, %s, 'no')", (case, conflict_key)
    )
    for field in ("party_role", "instructing_party_name"):
        admin.execute(
            "insert into app.prefill_suggestions (case_id, instruction_version_id, field, value, word_ids,"
            " model_task, prompt_version) values (%s, %s, %s, %s, '{w1}', 'T-04', 'p1')",
            (case, version[0], field, b"sealed"),
        )
    return {"role": role, "ip_type": ip_type, "kind": kind, "conflict": conflict_key}


async def test_keys_the_new_packs_lack_are_kept_and_listed(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection, app: FastAPI, tmp_path: Path
) -> None:
    app.state.packs = library_with_other_keys(tmp_path)
    keys = seed_keys(admin, world["case"], app.state.packs)
    client = await client_for(world["expert"])
    tag = (await client.get(f"/v1/cases/{world['case']}")).headers["etag"]
    body = {"jurisdiction": ALT, "case_type": ALT_CASE, "specialty": FIXTURE.specialty}
    resp = await client.patch(url(world, "jurisdiction"), json=body, headers={"if-match": tag})
    assert resp.status_code == 200, resp.text
    changes = resp.json()["changes"]
    for key, before in (
        ("case_parties.role_key", keys["role"]),
        ("case_instructing_parties.type_key", keys["ip_type"]),
        ("questions.kind", keys["kind"]),
        ("conflict_answers.question_key", keys["conflict"]),
    ):
        assert {"what": "held", "key": key, "before": before, "after": None} in changes, key
    assert {"what": "dismissed", "key": "prefill_suggestions.party_role", "before": None, "after": None} in changes

    # nothing is deleted or rewritten; the waiting key suggestion is dismissed, the name suggestion waits
    assert admin.execute("select role_key from app.case_parties where case_id = %s", (world["case"],)).fetchone() == (
        keys["role"],
    )
    assert admin.execute("select kind from app.questions where case_id = %s", (world["case"],)).fetchone() == (
        keys["kind"],
    )
    statuses = dict(
        admin.execute("select field, status from app.prefill_suggestions where case_id = %s", (world["case"],))
        .fetchall()
    )
    assert statuses == {"party_role": "dismissed", "instructing_party_name": "suggested"}


async def test_an_ended_instructing_party_is_not_listed(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection, app: FastAPI, tmp_path: Path
) -> None:
    app.state.packs = library_with_other_keys(tmp_path)
    keys = seed_keys(admin, world["case"], app.state.packs)
    admin.execute(
        "update app.case_instructing_parties set effective_from = current_date - 10, effective_to = current_date - 1"
        " where case_id = %s",
        (world["case"],),
    )
    client = await client_for(world["expert"])
    tag = (await client.get(f"/v1/cases/{world['case']}")).headers["etag"]
    body = {"jurisdiction": ALT, "case_type": ALT_CASE, "specialty": FIXTURE.specialty}
    changes = (await client.patch(url(world, "jurisdiction"), json=body, headers={"if-match": tag})).json()["changes"]
    assert not any(c["key"] == "case_instructing_parties.type_key" for c in changes)
    assert {"what": "held", "key": "case_parties.role_key", "before": keys["role"], "after": None} in changes
