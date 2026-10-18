"""Pack fields the setup screens (S02-S07) read instead of fixed words, under both pack combinations.

- jurisdiction vocabulary: `other_side_prompt`, `other_side_example`, `party_roles_plural` (one word per
  party role), `subject_status_hint`, `party_name_ignore_words` (words not part of a party's name);
- `report.yaml` `page_limit`: pages allowed per court track;
- case-type question types: `colour` (a design token name) and `parts` (the parts a question can be
  answered in, each with a label key);
- specialty: `outside_expertise_parts` (the parts to answer when a question is outside the expert's
  expertise), `outside_expertise_hint` and `coverage_question` (vocabulary words).
Expected values are read from the pack files, so nothing here names a country, case type or specialty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import packs
from tests.support.packs import BOTH, FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo, copy_packs

IDS = [c.jurisdiction for c in BOTH]
JURISDICTION_WORDS = ("other_side_prompt", "other_side_example", "subject_status_hint")


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


def merged(library: packs.PackLibrary, combo: Combo) -> packs.MergedPack:
    return library.combine(*combo.codes)


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_the_jurisdiction_words_come_from_its_vocabulary(library: packs.PackLibrary, combo: Combo) -> None:
    vocabulary = merged(library, combo).vocabulary
    raw = combo.raw("jurisdiction", "vocabulary.yaml")
    for key in JURISDICTION_WORDS:
        assert isinstance(raw[key], str) and raw[key]
        assert vocabulary[key] == raw[key]
    assert vocabulary["party_roles_plural"] == raw["party_roles_plural"]
    assert vocabulary["party_name_ignore_words"] == raw["party_name_ignore_words"]
    assert all(isinstance(w, str) and w for w in vocabulary["party_name_ignore_words"])
    roles = combo.raw("case-type", "pack.yaml")["party_roles"]
    assert set(roles) <= set(vocabulary["party_roles_plural"])


def test_the_jurisdiction_words_differ_between_the_launch_and_fixture_packs(library: packs.PackLibrary) -> None:
    launch, fixture = merged(library, LAUNCH).vocabulary, merged(library, FIXTURE).vocabulary
    for key in (*JURISDICTION_WORDS, "party_roles_plural", "party_name_ignore_words"):
        assert launch[key] != fixture[key], key


@pytest.mark.parametrize("key", [*JURISDICTION_WORDS, "party_roles_plural", "party_name_ignore_words"])
def test_a_jurisdiction_without_one_of_these_words_is_refused(tmp_path: Path, key: str) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    path = fixture_root / "jurisdiction" / FIXTURE.jurisdiction / "vocabulary.yaml"
    lines = path.read_text().splitlines(keepends=True)
    kept = [line for line in lines if not line.startswith(f"{key}:")]
    assert len(kept) < len(lines), key
    path.write_text("".join(kept))
    with pytest.raises(packs.PackError, match=key):
        packs.load_library(launch_root, fixture_root).combine(*FIXTURE.codes)


def test_a_plural_word_missing_for_a_party_role_is_refused(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    path = fixture_root / "jurisdiction" / FIXTURE.jurisdiction / "vocabulary.yaml"
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join("party_roles_plural: {instructing_side: x}\n" if l.startswith("party_roles_plural:") else l for l in lines))
    with pytest.raises(packs.PackError, match="other_side"):
        packs.load_library(launch_root, fixture_root).combine(*FIXTURE.codes)


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_report_page_limits_are_per_court_track_from_report_yaml(library: packs.PackLibrary, combo: Combo) -> None:
    limits = merged(library, combo).page_limits()
    raw = combo.raw("jurisdiction", "report.yaml")["page_limit"]
    assert limits == raw and limits
    assert all(isinstance(v, int) and v > 0 for v in limits.values())
    assert merged(library, combo).compliance_checklist()["page_limit"] == raw


def test_page_limits_differ_between_the_launch_and_fixture_packs(library: packs.PackLibrary) -> None:
    assert merged(library, LAUNCH).page_limits() != merged(library, FIXTURE).page_limits()


def test_a_jurisdiction_without_page_limits_has_none(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    path = fixture_root / "jurisdiction" / FIXTURE.jurisdiction / "report.yaml"
    path.write_text("".join(line for line in path.read_text().splitlines(keepends=True) if "page_limit" not in line))
    pack = packs.load_library(launch_root, fixture_root).combine(*FIXTURE.codes)
    assert pack.page_limits() == {}


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_every_question_type_has_a_colour_and_parts(library: packs.PackLibrary, combo: Combo) -> None:
    types = merged(library, combo).question_types()
    raw = {q["key"]: q for q in combo.raw("case-type", "questions.yaml")["question_types"]}
    assert types
    for q in types:
        assert q["colour"] == raw[q["key"]]["colour"]
        assert q["parts"] == raw[q["key"]]["parts"]
        assert q["parts"], q["key"]
        assert all(p["key"] and p["label_key"] for p in q["parts"])
        assert len({p["key"] for p in q["parts"]}) == len(q["parts"])


def test_question_colours_and_parts_differ_between_the_launch_and_fixture_packs(library: packs.PackLibrary) -> None:
    launch, fixture = merged(library, LAUNCH).question_types(), merged(library, FIXTURE).question_types()
    assert [q["parts"] for q in launch] != [q["parts"] for q in fixture]


def test_a_question_type_whose_parts_break_the_schema_is_refused(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    path = fixture_root / "case-type" / FIXTURE.case_type / "questions.yaml"
    path.write_text("question_types:\n  - {key: zz_a, label_key: zz.a, colour: x, parts: [{key: Bad Key}]}\n")
    with pytest.raises(packs.PackError, match="questions.yaml"):
        packs.load_library(launch_root, fixture_root)


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_outside_expertise_parts_are_the_specialtys_and_exist_in_the_case_type(
    library: packs.PackLibrary, combo: Combo
) -> None:
    pack = merged(library, combo)
    parts = pack.outside_expertise_parts()
    assert parts and parts == combo.raw("specialty", "pack.yaml")["outside_expertise_parts"]
    known = {p["key"] for q in pack.question_types() for p in q["parts"]}
    assert set(parts) <= known
    for key in ("outside_expertise_hint", "coverage_question"):
        assert pack.vocabulary[key] == combo.raw("specialty", "vocabulary.yaml")[key]
        assert pack.vocabulary[key]


def test_outside_expertise_words_differ_between_the_launch_and_fixture_packs(library: packs.PackLibrary) -> None:
    launch, fixture = merged(library, LAUNCH), merged(library, FIXTURE)
    assert launch.outside_expertise_parts() != fixture.outside_expertise_parts()
    for key in ("outside_expertise_hint", "coverage_question"):
        assert launch.vocabulary[key] != fixture.vocabulary[key]


def test_an_outside_expertise_part_no_question_type_has_is_refused(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    path = fixture_root / "specialty" / FIXTURE.specialty / "pack.yaml"
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join("outside_expertise_parts: [zz_no_such_part]\n" if l.startswith("outside_expertise_parts:") else l for l in lines))
    with pytest.raises(packs.PackError, match="zz_no_such_part"):
        packs.load_library(launch_root, fixture_root).combine(*FIXTURE.codes)


@pytest.mark.parametrize("combo", BOTH, ids=IDS)
def test_document_kinds_are_the_core_kinds_plus_the_case_types(library: packs.PackLibrary, combo: Combo) -> None:
    kinds = merged(library, combo).document_kinds()
    extra = combo.raw("case-type", "pack.yaml").get("document_kinds", [])
    assert [k["key"] for k in kinds] == ["instructions", "records", "other", *extra]
    assert all(k["label_key"] == f"document_kind.{k['key']}" for k in kinds)
