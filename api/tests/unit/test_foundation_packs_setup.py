"""Pack data slice C needs (docs/slices/0-foundation.md "Changes for slice C"), under both pack combinations.

- `conflicts()`: the jurisdiction's conflict-of-interest questions and the statement wording they feed;
- `coverage_roles()`: which kinds of role in the specialty count when checking the expert practised
  during the case period;
- `outside_expertise_question_types()`: question types the specialty marks as usually outside its expertise;
- `prompt_context(task)`: what T-02, T-04 and T-06 get from the packs (other tasks get the default);
- `normalise_identifier()`: the jurisdiction's canonical form of a subject identifier, for the duplicate check.
Expected values are read from the pack files, so nothing here names a country, case type or specialty.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import packs
from tests.support.packs import BOTH, FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo, copy_packs


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


def merged(library: packs.PackLibrary, combo: Combo) -> packs.MergedPack:
    return library.combine(*combo.codes)


IDS = [c.jurisdiction for c in BOTH]


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_conflict_questions_and_statements_come_from_the_jurisdiction(library: packs.PackLibrary, combo: Combo) -> None:
    raw = combo.raw("jurisdiction", "conflicts.yaml")
    conflicts = merged(library, combo).conflicts()
    assert [q["key"] for q in conflicts["questions"]] == [q["key"] for q in raw["questions"]]
    assert all(q["text"] and isinstance(q["detail_required"], bool) for q in conflicts["questions"])
    assert set(conflicts["statement"]) == {"none", "disclosed"}
    assert all(conflicts["statement"].values())
    assert not any(re.search(r"\{[a-z_]+\}", q["text"]) for q in conflicts["questions"])


def test_conflict_questions_differ_between_the_launch_and_fixture_packs(library: packs.PackLibrary) -> None:
    assert merged(library, LAUNCH).conflicts() != merged(library, FIXTURE).conflicts()


def test_a_jurisdiction_without_conflict_questions_has_none(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    (fixture_root / "jurisdiction" / FIXTURE.jurisdiction / "conflicts.yaml").unlink()
    pack = packs.load_library(launch_root, fixture_root).combine(*FIXTURE.codes)
    assert pack.conflicts() == {"questions": [], "statement": {}}


def test_a_conflicts_file_that_breaks_its_schema_is_refused(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    (fixture_root / "jurisdiction" / FIXTURE.jurisdiction / "conflicts.yaml").write_text("questions: [{key: 1}]\n")
    with pytest.raises(packs.PackError, match="conflicts.yaml"):
        packs.load_library(launch_root, fixture_root)


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_coverage_roles_come_from_the_specialty_with_labels(library: packs.PackLibrary, combo: Combo) -> None:
    roles = merged(library, combo).coverage_roles()
    assert [r["key"] for r in roles] == combo.raw("specialty", "pack.yaml")["coverage_roles"]
    assert all(r["label"] == combo.raw("specialty", "vocabulary.yaml")["coverage_roles"][r["key"]] for r in roles)
    assert merged(library, LAUNCH).coverage_roles() != merged(library, FIXTURE).coverage_roles()


def test_a_coverage_role_without_a_label_is_refused(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    path = fixture_root / "specialty" / FIXTURE.specialty / "pack.yaml"
    path.write_text(path.read_text().replace("coverage_roles: [", "coverage_roles: [unlabelled, "))
    with pytest.raises(packs.PackError, match="unlabelled"):
        packs.load_library(launch_root, fixture_root).combine(*FIXTURE.codes)


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_outside_expertise_question_types_are_the_specialtys_and_exist_in_the_case_type(
    library: packs.PackLibrary, combo: Combo
) -> None:
    pack = merged(library, combo)
    outside = pack.outside_expertise_question_types()
    assert outside and outside == combo.raw("specialty", "pack.yaml")["outside_expertise_question_types"]
    assert set(outside) < {q["key"] for q in pack.question_types()}
    assert merged(library, LAUNCH).outside_expertise_question_types() != (
        merged(library, FIXTURE).outside_expertise_question_types()
    )


# --- prompt context per task ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_other_tasks_get_the_default_prompt_context(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    assert pack.prompt_context("T-07b") == pack.prompt_context(None) == pack.prompt_context()


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_t06_gets_the_question_types_and_what_is_outside_expertise(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    context = pack.prompt_context("T-06")
    assert context["question_types"] == [q["key"] for q in combo.raw("case-type", "questions.yaml")["question_types"]]
    assert context["outside_expertise_question_types"] == pack.outside_expertise_question_types()
    assert context["language"] == combo.raw("jurisdiction", "pack.yaml")["language"]
    assert "fact_kinds" not in context


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_t04_gets_the_allowed_party_words_and_the_date_order(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    context = pack.prompt_context("T-04")
    assert context["instructing_party_types"] == pack.instructing_party_types()
    assert context["party_roles"] == pack.party_roles()
    assert context["date_order"] == combo.raw("jurisdiction", "pack.yaml")["date_order"]
    assert context["court_tracks"] == combo.raw("jurisdiction", "pack.yaml").get("court_tracks", [])


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_t02_maps_headings_to_the_pack_report_sections_or_other(library: packs.PackLibrary, combo: Combo) -> None:
    sections = combo.raw("case-type", "report_sections.yaml")["sections"]
    context = merged(library, combo).prompt_context("T-02")
    assert context["report_sections"] == [*sections, "other"]


# --- the subject identifier for the duplicate check -----------------------------------------------------------------


def test_the_launch_identifier_is_normalised_by_its_pack(library: packs.PackLibrary) -> None:
    pack = merged(library, LAUNCH)
    assert pack.normalise_identifier("943 476 5919") == pack.normalise_identifier("943-476-5919") == "9434765919"
    assert pack.normalise_identifier("943 476 5918") is None
    assert merged(library, FIXTURE).normalise_identifier("943 476 5919") is None


def test_the_fixture_identifier_is_normalised_by_its_pack(library: packs.PackLibrary) -> None:
    pack = merged(library, FIXTURE)
    assert pack.normalise_identifier("  zz-id-123456 ") == "ZZ-ID-123456"
    assert pack.normalise_identifier("ZZ-ID-12345") is None
    assert merged(library, LAUNCH).normalise_identifier("ZZ-ID-123456") is None


def test_a_jurisdiction_without_a_normaliser_has_no_identifier_to_match(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    path = fixture_root / "jurisdiction" / FIXTURE.jurisdiction / "identifiers.py"
    path.write_text(path.read_text().split("\ndef normalise_identifier", 1)[0] + "\n")
    pack = packs.load_library(launch_root, fixture_root).combine(*FIXTURE.codes)
    assert pack.normalise_identifier("ZZ-ID-123456") is None
