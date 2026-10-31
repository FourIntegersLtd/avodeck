"""Specialty pack files for the records transcript (ADR 0028): entry kinds, author roles, specialties,
result groups and reading prompts. Each runs under the launch and the fixture packs; expected values are
read from the pack files (rule 11)."""

from __future__ import annotations

import json
import re

import jsonschema_rs
import pytest

from app.core import packs
from tests.support.packs import BOTH, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo

KEY = re.compile(r"^[a-z][a-z0-9_]*$")
I18N_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
ADMIN_AT_LAUNCH = {
    "appointment_booking",
    "registration",
    "administrative_task",
    "repeat_prescription_request",
    "appointment_letter",
}


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


def merged(library: packs.PackLibrary, combo: Combo) -> packs.MergedPack:
    return library.combine(*combo.codes)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_entry_kinds_come_from_the_pack_with_a_transcript_flag(library: packs.PackLibrary, combo: Combo) -> None:
    raw = combo.raw("specialty", "entries.yaml")["entry_kinds"]
    kinds = merged(library, combo).entry_kinds()
    assert [k["key"] for k in kinds] == [k["key"] for k in raw if k.get("enabled", True)]
    assert kinds
    for kind in kinds:
        assert KEY.match(kind["key"])
        assert kind["transcript"] in ("include", "exclude")
        assert I18N_KEY.match(kind["label"]), kind["label"]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_transcript_kinds_are_the_included_ones(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    included = {k["key"] for k in pack.entry_kinds() if k["transcript"] == "include"}
    excluded = {k["key"] for k in pack.entry_kinds() if k["transcript"] == "exclude"}
    assert pack.transcript_entry_kinds() == frozenset(included)
    assert included and excluded, "a pack names both what is shown and what is folded"


def test_the_launch_pack_folds_the_admin_kinds_and_has_a_letter_kind(library: packs.PackLibrary) -> None:
    pack = merged(library, LAUNCH)
    keys = {k["key"] for k in pack.entry_kinds()}
    assert ADMIN_AT_LAUNCH <= keys
    assert not ADMIN_AT_LAUNCH & pack.transcript_entry_kinds()
    assert pack.letter_entry_kind() == "letter"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_each_pack_names_exactly_one_letter_kind_and_it_is_shown(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    flagged = [k["key"] for k in pack.entry_kinds() if k.get("letter") is True]
    assert flagged == [pack.letter_entry_kind()]
    assert pack.letter_entry_kind() in pack.transcript_entry_kinds()


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_author_roles_and_specialties_are_keys_with_label_keys(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    assert pack.author_roles() and pack.entry_specialties()
    for item in [*pack.author_roles(), *pack.entry_specialties()]:
        assert KEY.match(item["key"])
        assert I18N_KEY.match(item["label"]), item["label"]
    raw_roles = combo.raw("specialty", "entries.yaml")["author_roles"]
    assert [r["key"] for r in pack.author_roles()] == [r["key"] for r in raw_roles]
    raw_specialties = combo.raw("specialty", "specialties.yaml")["specialties"]
    assert [s["key"] for s in pack.entry_specialties()] == [s["key"] for s in raw_specialties]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_result_groups_and_reading_prompts_list_their_terms(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    for group in [*pack.result_groups(), *pack.reading_prompt_groups()]:
        assert KEY.match(group["key"])
        assert I18N_KEY.match(group["label"]), group["label"]
        assert group["terms"] and all(isinstance(t, str) and t.strip() for t in group["terms"])
    raw = combo.raw("specialty", "result_groups.yaml")["result_groups"]
    assert [g["key"] for g in pack.result_groups()] == [g["key"] for g in raw]
    raw = combo.raw("specialty", "reading_prompts.yaml")["term_groups"]
    assert [g["key"] for g in pack.reading_prompt_groups()] == [g["key"] for g in raw]
    assert pack.result_groups() and pack.reading_prompt_groups()


def test_the_launch_pack_has_the_named_results_and_prompts(library: packs.PackLibrary) -> None:
    pack = merged(library, LAUNCH)
    assert {"hba1c", "creatinine", "egfr"} <= {g["key"] for g in pack.result_groups()}
    assert {"pain", "injury", "accident", "disability", "psychological"} <= {
        g["key"] for g in pack.reading_prompt_groups()
    }
    hba1c = next(g for g in pack.result_groups() if g["key"] == "hba1c")
    assert "glycated haemoglobin" in hba1c["terms"]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_extraction_prompt_context_names_the_entry_vocabulary(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    context = pack.prompt_context("T-07b")
    assert [k["key"] for k in context["entry_kinds"]] == [k["key"] for k in pack.entry_kinds()]
    assert context["author_roles"] == [r["key"] for r in pack.author_roles()]
    assert context["specialties"] == [s["key"] for s in pack.entry_specialties()]
    assert "fact_kinds" in context, "T-07b keeps the default context"


@pytest.mark.parametrize(
    ("schema", "bad"),
    [
        ("entries", {"entry_kinds": [{"key": "note", "label": "a.b"}], "author_roles": []}),  # no transcript flag
        ("entries", {"entry_kinds": [{"key": "note", "label": "a.b", "transcript": "maybe"}], "author_roles": []}),
        ("specialties", {"specialties": [{"key": "Bad Key", "label": "a.b"}]}),
        ("result_groups", {"result_groups": [{"key": "x", "label": "a.b", "terms": []}]}),
        ("reading_prompts", {"term_groups": [{"key": "x", "label": "a.b"}]}),
    ],
)
def test_the_schemas_refuse_a_file_that_does_not_fit(schema: str, bad: dict[str, object]) -> None:
    validator = jsonschema_rs.validator_for(json.loads((LAUNCH_ROOT / "schema" / f"{schema}.schema.json").read_text()))
    assert not validator.is_valid(bad)
