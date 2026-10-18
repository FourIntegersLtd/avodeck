"""The pack loader (architecture 3.2, 3.5, 3.6): four layers, merge rules, `extends:`, placeholders, schemas, lint.

Fixture-pack words are written here (they exist only for tests); launch-pack words are read from the
pack files, so nothing in this file depends on a country, case type or specialty.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.core import packs
from tests.support.packs import BOTH, FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo, copy_packs, write

API = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


def merged(library: packs.PackLibrary, combo: Combo) -> packs.MergedPack:
    return library.combine(*combo.codes)


def keys(items: list[dict[str, object]]) -> list[object]:
    return [item["key"] for item in items]


# --- loading and the four layers ----------------------------------------------------------------------------------


def test_launch_and_fixture_packs_load_and_validate(library: packs.PackLibrary) -> None:
    for combo in BOTH:
        pack = merged(library, combo)
        assert (
            pack.versions["jurisdiction"] == f"{combo.jurisdiction}@{combo.raw('jurisdiction', 'pack.yaml')['version']}"
        )
        assert pack.versions["case_type"] == f"{combo.case_type}@{combo.raw('case-type', 'pack.yaml')['version']}"
        assert pack.versions["specialty"] == f"{combo.specialty}@{combo.raw('specialty', 'pack.yaml')['version']}"


def test_the_overlay_is_the_fourth_layer_and_is_optional(library: packs.PackLibrary) -> None:
    launch = merged(library, LAUNCH)
    overlay = LAUNCH.raw("overlay", "pack.yaml")
    assert launch.versions["overlay"] == f"{LAUNCH.jurisdiction}/{LAUNCH.specialty}@{overlay['version']}"
    assert "overlay" not in merged(library, FIXTURE).versions


def test_for_case_returns_the_merged_pack_for_a_case_row_and_caches_it(library: packs.PackLibrary) -> None:
    pack = library.for_case(FIXTURE.case_row())
    assert pack is library.for_case(FIXTURE.case_row())
    assert pack.versions == merged(library, FIXTURE).versions


def test_an_unknown_pack_code_is_refused(library: packs.PackLibrary) -> None:
    with pytest.raises(packs.PackError, match="no-such-place"):
        library.combine("no-such-place", FIXTURE.case_type, FIXTURE.specialty)


# --- what each layer provides, under both the launch and the fixture packs ----------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_jurisdiction_settings_come_from_the_jurisdiction_pack(library: packs.PackLibrary, combo: Combo) -> None:
    pack, meta = merged(library, combo), combo.raw("jurisdiction", "pack.yaml")
    assert pack.timezone == meta["timezone"]
    assert pack.date_order == meta["date_order"]
    assert pack.locale == meta["locale"]
    assert pack.currency == meta["currency"]
    assert pack.retention_years == meta["default_retention_years"]
    assert pack.speech()["locale"] == meta["speech"]["locale"]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_party_and_instructing_party_words_come_from_the_jurisdiction_vocabulary(
    library: packs.PackLibrary, combo: Combo
) -> None:
    pack = merged(library, combo)
    vocab, case_type = combo.raw("jurisdiction", "vocabulary.yaml"), combo.raw("case-type", "pack.yaml")
    assert pack.party_roles() == [{"key": k, "label": vocab["party_roles"][k]} for k in case_type["party_roles"]]
    assert pack.instructing_party_types() == [
        {"key": k, "label": vocab["instructing_party_types"][k]} for k in case_type["instructing_party_types"]
    ]
    assert pack.term("subject") == vocab["subject"]
    assert pack.expert_roles() == case_type["expert_roles"]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_clinician_title_is_the_latest_layer_that_sets_it(library: packs.PackLibrary, combo: Combo) -> None:
    assert merged(library, combo).term("clinician") == combo.word("clinician")


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_registration_bodies_follow_the_specialty_profession(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    profession = combo.raw("specialty", "pack.yaml")["profession"]
    assert pack.profession == profession
    assert pack.registration_bodies() == combo.raw("jurisdiction", "pack.yaml")["registration_bodies"][profession]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_registries_come_from_the_packs(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    assert keys(pack.workflow_steps()) == [s["key"] for s in combo.raw("case-type", "workflow.yaml")["steps"]]
    assert pack.workflow_steps()[0]["screens"] == combo.raw("case-type", "workflow.yaml")["steps"][0]["screens"]
    assert keys(pack.question_types()) == [q["key"] for q in combo.raw("case-type", "questions.yaml")["question_types"]]
    assert keys(pack.report_sections()) == [s["key"] for s in combo.raw("case-type", "report_sections.yaml")["sections"]]
    assert keys(pack.fact_kinds()) == [f["key"] for f in combo.raw("specialty", "facts.yaml")["fact_kinds"]]
    assert all(isinstance(f["fields"], dict) and f["fields"] for f in pack.fact_kinds())
    sources = combo.raw("specialty", "sources.yaml")["source_types"]
    assert keys(pack.source_types()) == [s["key"] for s in sources]
    lanes = combo.raw("specialty", "lanes.yaml")["lanes"]
    assert keys(pack.lanes()) == [lane if isinstance(lane, str) else lane["key"] for lane in lanes]
    checklist = combo.raw("jurisdiction", "report.yaml")
    assert keys(pack.compliance_checklist()["items"]) == [i["key"] for i in checklist["required_contents"]]
    assert pack.compliance_checklist()["rule_ref"] == checklist["rule_ref"]


def test_launch_and_fixture_packs_differ_in_every_pack_driven_value(library: packs.PackLibrary) -> None:
    """The core guard for rule 11: a value that comes out the same under both packs is not coming from the pack."""
    launch, fixture = merged(library, LAUNCH), merged(library, FIXTURE)
    for attr in ("timezone", "date_order", "locale", "currency", "retention_years", "profession"):
        assert getattr(launch, attr) != getattr(fixture, attr), attr
    for term in ("subject", "clinician", "court_bundle", "written_questions", "declaration_name"):
        assert launch.term(term) != fixture.term(term), term
    for registry in ("workflow_steps", "question_types", "fact_kinds", "lanes", "party_roles", "declarations"):
        assert getattr(launch, registry)() != getattr(fixture, registry)(), registry
    assert launch.registration_bodies() != fixture.registration_bodies()
    assert launch.deadline_rules() != fixture.deadline_rules()


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_declarations_are_the_pack_text_with_their_front_matter(library: packs.PackLibrary, combo: Combo) -> None:
    raw = combo.raw("jurisdiction", "declarations.md")
    [declaration] = merged(library, combo).declarations()
    front = re.search(r"^key:\s*(\S+)$", raw, re.M)
    assert front is not None and declaration["key"] == front.group(1)
    assert declaration["text"] and declaration["text"] in raw
    assert "---" not in declaration["text"]


def test_identifier_recognisers_come_from_the_jurisdiction_pack(library: packs.PackLibrary) -> None:
    launch, fixture = (
        merged(library, LAUNCH).identifier_recognisers(),
        merged(library, FIXTURE).identifier_recognisers(),
    )
    launch_text, fixture_text = "ref 943 476 5919 here", "ref ZZ-ID-123456 here"
    assert [s[:2] for s in launch(launch_text)] == [(4, 16)]
    assert launch(fixture_text) == []
    assert [s[:2] for s in fixture(fixture_text)] == [(4, 16)]
    assert fixture(launch_text) == []


def test_prompt_context_carries_pack_data_only(library: packs.PackLibrary) -> None:
    context = merged(library, FIXTURE).prompt_context()
    assert context["date_order"] == "MDY"
    assert context["language"] == FIXTURE.raw("jurisdiction", "pack.yaml")["language"]
    assert [k["key"] for k in context["fact_kinds"]] == ["session", "measurement"]
    assert context["fact_kinds"][0]["fields"] == {"duration_minutes": "integer"}
    assert [s["key"] for s in context["source_types"]] == ["clinic_notes", "letters", "zz_account"]
    assert context["vocabulary"]["clinician"] == "therapist"


def test_record_hints_and_speech_phrases_come_from_the_overlay(library: packs.PackLibrary) -> None:
    launch = merged(library, LAUNCH)
    records = LAUNCH.raw("overlay", "records.yaml")
    assert launch.record_hints() == records
    assert launch.speech()["phrases"] == LAUNCH.raw("overlay", "speech_phrases.yaml")["phrases"]
    assert merged(library, FIXTURE).record_hints() == {}
    assert merged(library, FIXTURE).speech()["phrases"] == []


# --- merge rules (3.2), extends and placeholders, on a writable copy of the packs ----------------------------------


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    return copy_packs(tmp_path)


def test_registries_union_by_key_and_a_later_layer_can_disable_an_entry(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(
        fixtures / "overlay/zz-test/zz-specialty/pack.yaml",
        "jurisdiction: zz-test\nspecialty: zz-specialty\nversion: 0.0.1\n",
    )
    write(
        fixtures / "overlay/zz-test/zz-specialty/lanes.yaml",
        "lanes:\n  - {key: letters, enabled: false}\n  - {key: home_visits}\n",
    )
    write(fixtures / "overlay/zz-test/zz-specialty/vocabulary.yaml", "clinician: zz-local-title\n")
    pack = packs.load_library(fixtures).combine(*FIXTURE.codes)
    assert keys(pack.lanes()) == ["sessions", "measurements", "home_visits"]
    assert pack.term("clinician") == "zz-local-title"
    assert pack.term("clinicians") == "therapists"  # dictionaries merge by key: the rest stays


def test_final_checks_from_three_layers_are_unioned(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(fixtures / "jurisdiction/zz-test/final_checks.yaml", "checks:\n  - {key: j_check, text: J}\n")
    write(fixtures / "case-type/zz-case/final_checks.yaml", "checks:\n  - {key: c_check, text: C}\n")
    write(
        fixtures / "specialty/zz-specialty/final_checks.yaml",
        "checks:\n  - {key: s_check, text: S}\n  - {key: j_check, text: J2}\n",
    )
    pack = packs.load_library(fixtures).combine(*FIXTURE.codes)
    assert [(c["key"], c["text"]) for c in pack.final_checks()] == [
        ("j_check", "J2"),
        ("c_check", "C"),
        ("s_check", "S"),
    ]


def test_text_files_are_replaced_whole_by_a_later_layer(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(
        fixtures / "case-type/zz-case/declarations.md",
        "---\nkey: case_affirmation\nrule_ref: Rule Z.10\nversion: 0.0.1\n---\nA case-type declaration.\n",
    )
    [declaration] = packs.load_library(fixtures).combine(*FIXTURE.codes).declarations()
    assert declaration == {
        "key": "case_affirmation",
        "rule_ref": "Rule Z.10",
        "version": "0.0.1",
        "text": "A case-type declaration.",
    }


def test_a_pack_can_extend_another_in_its_layer(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(
        fixtures / "jurisdiction/zz-child/pack.yaml",
        "code: zz-child\nname: Test child\nversion: 0.0.1\nextends: zz-test\ntimezone: Asia/Tokyo\n",
    )
    write(fixtures / "jurisdiction/zz-child/vocabulary.yaml", "subject: ward\n")
    pack = packs.load_library(fixtures).combine("zz-child", FIXTURE.case_type, FIXTURE.specialty)
    assert pack.timezone == "Asia/Tokyo"
    assert pack.date_order == "MDY"  # inherited
    assert pack.term("subject") == "ward"
    assert pack.term("court_bundle") == "hearing binder"  # inherited key by key
    assert pack.deadline_rules() == packs.load_library(fixtures).combine(*FIXTURE.codes).deadline_rules()
    assert pack.identifier_recognisers()("ZZ-ID-123456")
    assert pack.versions["jurisdiction"] == "zz-child@0.0.1"


def test_extending_a_missing_pack_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(
        fixtures / "jurisdiction/zz-child/pack.yaml", "code: zz-child\nname: C\nversion: 0.0.1\nextends: zz-nowhere\n"
    )
    with pytest.raises(packs.PackError, match="zz-nowhere"):
        packs.load_library(fixtures)


def test_placeholders_are_filled_from_other_layers_at_merge_time(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(
        fixtures / "case-type/zz-case/final_checks.yaml",
        "checks:\n  - {key: asked, text: 'Ask the {instructing_party} what each {clinician} saw of the {subject}'}\n",
    )
    [check] = packs.load_library(fixtures).combine(*FIXTURE.codes).final_checks()
    assert check["text"] == "Ask the carrier what each therapist saw of the petitioner"


def test_launch_legal_tests_have_their_placeholders_filled(library: packs.PackLibrary) -> None:
    text = merged(library, LAUNCH).legal_tests()
    assert "{" not in text
    assert LAUNCH.word("clinicians") in text


def test_a_placeholder_that_resolves_to_nothing_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(fixtures / "case-type/zz-case/final_checks.yaml", "checks:\n  - {key: odd, text: 'Ask the {nobody}'}\n")
    with pytest.raises(packs.PackError, match="nobody"):
        packs.load_library(fixtures)


def test_every_party_role_needs_a_label_in_the_jurisdiction(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    vocab = fixtures / "jurisdiction/zz-test/vocabulary.yaml"
    vocab.write_text(vocab.read_text().replace("other_side: respondent, ", ""))
    with pytest.raises(packs.PackError, match="other_side"):
        packs.load_library(fixtures)


# --- refusals: schema, duplicates, lint ---------------------------------------------------------------------------


def test_a_key_defined_twice_in_one_file_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    vocab = fixtures / "jurisdiction/zz-test/vocabulary.yaml"
    vocab.write_text(vocab.read_text() + "subject: someone else\n")
    with pytest.raises(packs.PackError, match="subject"):
        packs.load_library(fixtures)


def test_a_registry_key_defined_twice_in_one_layer_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(fixtures / "specialty/zz-specialty/lanes.yaml", "lanes: [sessions, measurements, sessions]\n")
    with pytest.raises(packs.PackError, match="sessions"):
        packs.load_library(fixtures)


@pytest.mark.parametrize(
    ("relpath", "find", "replace"),
    [
        ("jurisdiction/zz-test/pack.yaml", "date_order: MDY", "date_order: sideways"),
        ("jurisdiction/zz-test/pack.yaml", "timezone: America/Halifax", "timezone: Mars/Olympus"),
        ("specialty/zz-specialty/pack.yaml", "profession: therapist", "profession: [therapist]"),
        ("case-type/zz-case/workflow.yaml", "key: gather", "name: gather"),
    ],
)
def test_a_file_that_breaks_its_schema_is_refused(
    tree: tuple[Path, Path], relpath: str, find: str, replace: str
) -> None:
    _, fixtures = tree
    path = fixtures / relpath
    assert find in path.read_text()
    path.write_text(path.read_text().replace(find, replace))
    with pytest.raises(packs.PackError, match=re.escape(relpath)):
        packs.load_library(fixtures)


def test_a_missing_required_file_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    (fixtures / "jurisdiction/zz-test/deadlines.yaml").unlink()
    with pytest.raises(packs.PackError, match="deadlines"):
        packs.load_library(fixtures)


def test_a_file_without_a_schema_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(fixtures / "specialty/zz-specialty/surprise.yaml", "anything: goes\n")
    with pytest.raises(packs.PackError, match="surprise.yaml"):
        packs.load_library(fixtures)


def test_a_jurisdiction_file_with_a_specialty_word_is_refused(tree: tuple[Path, Path]) -> None:
    launch, _ = tree
    word = packs.lint_words(launch)["specialty_words"][0]
    vocab = launch / "jurisdiction/gb-eaw/vocabulary.yaml"
    vocab.write_text(vocab.read_text() + f"helper: ask the {word}\n")
    with pytest.raises(packs.PackError, match=re.escape(word)):
        packs.load_library(launch)


def test_a_jurisdiction_file_naming_a_specialty_code_is_refused(tree: tuple[Path, Path]) -> None:
    launch, _ = tree
    meta = launch / "jurisdiction/gb-eaw/pack.yaml"
    meta.write_text(meta.read_text() + f"# see {LAUNCH.specialty}\n")
    with pytest.raises(packs.PackError, match=LAUNCH.specialty):
        packs.load_library(launch)


def test_a_case_type_file_with_a_specialty_word_is_refused(tree: tuple[Path, Path]) -> None:
    launch, _ = tree
    word = packs.lint_words(launch)["specialty_words"][-1]
    checks = launch / "case-type/clin-neg-breach/final_checks.yaml"
    checks.write_text(checks.read_text() + f"  - key: extra\n    text: The {word} view\n")
    with pytest.raises(packs.PackError, match=re.escape(word)):
        packs.load_library(launch)


def test_a_specialty_file_with_a_country_word_is_refused(tree: tuple[Path, Path]) -> None:
    launch, _ = tree
    word = packs.lint_words(launch)["country_words"][0]
    vocab = launch / "specialty/general-practice/vocabulary.yaml"
    vocab.write_text(vocab.read_text() + f"helper: the {word} way\n")
    with pytest.raises(packs.PackError, match=re.escape(word)):
        packs.load_library(launch)


def test_overlays_may_use_any_word(library: packs.PackLibrary) -> None:
    words = packs.lint_words(LAUNCH_ROOT)
    overlay_text = " ".join(p.read_text() for p in (LAUNCH_ROOT / "overlay").rglob("*.yaml"))
    assert packs.find_words(overlay_text, words["specialty_words"] + words["country_words"])  # and it loaded


def test_word_matching_respects_acronym_case_and_word_edges() -> None:
    assert packs.find_words("the GP said", ["GP"]) == ["GP"]
    assert packs.find_words("a GPU and gp_actions", ["GP"]) == []
    assert packs.find_words("Statement Of Truth", ["statement of truth"]) == ["statement of truth"]
    assert packs.find_words("hemisphere", ["EMIS"]) == []


# --- start-up and CI ----------------------------------------------------------------------------------------------


def test_the_app_refuses_to_start_with_an_invalid_pack(
    tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import create_app  # importing builds the module-level app: do it before breaking the pack

    launch, _ = tree
    meta = launch / "jurisdiction/gb-eaw/pack.yaml"
    meta.write_text(meta.read_text().replace("date_order: DMY", "date_order: 42"))
    monkeypatch.setenv("PACKS_DIR", str(launch))

    with pytest.raises(packs.PackError, match="date_order"):
        create_app()


def test_a_real_cell_loads_only_the_launch_packs_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PACKS_DIR", raising=False)
    library = packs.default_library()
    assert library.codes("jurisdiction") == [d.name for d in sorted((LAUNCH_ROOT / "jurisdiction").iterdir())]


def check_cli(*roots: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(API)}
    cmd = [sys.executable, "-m", "app.core.packs", *map(str, roots)]
    return subprocess.run(cmd, cwd=API, env=env, capture_output=True, text=True, check=False)


def test_the_ci_check_passes_on_the_real_packs() -> None:
    result = check_cli(LAUNCH_ROOT, FIXTURE_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_ci_check_fails_on_an_invalid_pack(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    (fixtures / "specialty/zz-specialty/lanes.yaml").write_text("lanes: 7\n")
    result = check_cli(fixtures)
    assert result.returncode == 1
    assert "lanes.yaml" in result.stdout + result.stderr
