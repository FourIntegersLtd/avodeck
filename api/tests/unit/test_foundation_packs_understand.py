"""What slice D (Understand) reads from the packs (architecture 3, slice brief D): lanes with their fact
kinds, lane labels and the "as seen" title, parameterised checks, guideline versions, own-record
sources and fact kinds that arrive from outside the clinician.

Fixture-pack words are written here (they exist only for tests); launch-pack values are read from the
pack files, so nothing in this file depends on a country, case type or specialty (rule 11).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core import packs
from tests.support.packs import BOTH, FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo, copy_packs, write

RULES = {
    "no_action_within",
    "not_acknowledged_within",
    "monitoring_overdue",
    "not_reached_clinician",
    "actions_not_done",
    "field_missing",
    "not_followed_up",
}
KIND_LISTS = ("action_kinds", "acknowledged_kinds", "followup_kinds", "or_kinds")


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    return copy_packs(tmp_path)


def merged(library: packs.PackLibrary, combo: Combo) -> packs.MergedPack:
    return library.combine(*combo.codes)


def raw_items(combo: Combo, layer: str, name: str, field: str) -> list[dict[str, Any]]:
    data = combo.raw(layer, name) or {}
    return [{"key": i} if isinstance(i, str) else i for i in data.get(field, [])]


def by_key(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["key"]: i for i in items}


def applies(check: dict[str, Any]) -> list[str]:
    value = check.get("applies_to", [])
    return [value] if isinstance(value, str) else list(value)


# --- lanes carry their fact kinds ---------------------------------------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_lanes_carry_the_fact_kinds_and_conditions_the_pack_gives(library: packs.PackLibrary, combo: Combo) -> None:
    lanes = merged(library, combo).lanes()
    raw = raw_items(combo, "specialty", "lanes.yaml", "lanes")
    assert [lane["key"] for lane in lanes] == [r["key"] for r in raw]
    for lane, written in zip(lanes, raw, strict=True):
        assert lane["kinds"] == written["kinds"], lane["key"]
        assert lane.get("when") == written.get("when"), lane["key"]
    assert all(lane["kinds"] for lane in lanes)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_every_kind_a_lane_names_is_a_fact_kind(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    kinds = {k["key"] for k in pack.fact_kinds()}
    assert {kind for lane in pack.lanes() for kind in lane["kinds"]} <= kinds


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_conditional_lane_shares_kinds_with_a_lane_without_the_condition(
    library: packs.PackLibrary, combo: Combo
) -> None:
    """A lane with `when` takes a fact first; the same kind without the condition falls to a plain lane."""
    lanes = merged(library, combo).lanes()
    conditional = [lane for lane in lanes if lane.get("when")]
    assert conditional, "each pack set shows a lane chosen by a field value"
    plain_kinds = {k for lane in lanes if not lane.get("when") for k in lane["kinds"]}
    for lane in conditional:
        assert set(lane["kinds"]) & plain_kinds, lane["key"]


def test_launch_and_fixture_lanes_hold_different_kinds(library: packs.PackLibrary) -> None:
    launch, fixture = merged(library, LAUNCH).lanes(), merged(library, FIXTURE).lanes()
    assert [lane["kinds"] for lane in launch] != [lane["kinds"] for lane in fixture]


def test_a_lane_can_still_be_a_plain_key(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    lanes = fixtures / "specialty/zz-specialty/lanes.yaml"
    write(lanes, lanes.read_text() + "  - zz_plain\n")
    pack = packs.load_library(fixtures).combine(*FIXTURE.codes)
    assert pack.lanes()[-1]["key"] == "zz_plain"
    assert pack.lanes()[-1].get("kinds", []) == []


def test_a_lane_naming_an_unknown_fact_kind_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(
        fixtures / "specialty/zz-specialty/lanes.yaml",
        "lanes:\n  - {key: sessions, kinds: [session, zz_nothing]}\n",
    )
    with pytest.raises(packs.PackError, match="zz_nothing"):
        packs.load_library(fixtures)


def test_a_lane_with_an_unknown_field_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(fixtures / "specialty/zz-specialty/lanes.yaml", "lanes:\n  - {key: sessions, kinds: [session], zz: 1}\n")
    with pytest.raises(packs.PackError, match="lanes.yaml"):
        packs.load_library(fixtures)


# --- lane labels and the "as seen" title come from vocabulary -----------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_every_lane_has_a_label_in_the_merged_vocabulary(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    labels = pack.vocabulary["lanes"]
    for lane in pack.lanes():
        assert isinstance(labels[lane["key"]], str) and labels[lane["key"]], lane["key"]


def test_the_overlay_lane_label_wins_over_the_specialty_one(library: packs.PackLibrary) -> None:
    overlay = LAUNCH.raw("overlay", "vocabulary.yaml")["lanes"]
    labels = merged(library, LAUNCH).vocabulary["lanes"]
    for key, label in overlay.items():
        assert labels[key] == label
    specialty = LAUNCH.raw("specialty", "vocabulary.yaml")["lanes"]
    assert set(specialty) >= {lane["key"] for lane in merged(library, LAUNCH).lanes()}


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_as_seen_title_is_a_vocabulary_word_with_placeholders_filled(
    library: packs.PackLibrary, combo: Combo
) -> None:
    title = merged(library, combo).term("as_seen_title")
    assert title and "{" not in title


def test_the_as_seen_title_and_lane_labels_differ_between_launch_and_fixture(library: packs.PackLibrary) -> None:
    launch, fixture = merged(library, LAUNCH), merged(library, FIXTURE)
    assert launch.term("as_seen_title") != fixture.term("as_seen_title")
    assert launch.vocabulary["lanes"] != fixture.vocabulary["lanes"]


def test_the_fixture_as_seen_title_carries_the_fixture_clinician_word(library: packs.PackLibrary) -> None:
    fixture = merged(library, FIXTURE)
    assert fixture.term("clinician") in fixture.term("as_seen_title")


# --- checks carry the parameters their rule types need ------------------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_checks_use_only_the_engine_rule_types(library: packs.PackLibrary, combo: Combo) -> None:
    checks = merged(library, combo).checks()
    assert checks
    assert {c["rule"] for c in checks} <= RULES


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_every_kind_a_check_names_is_a_fact_kind(library: packs.PackLibrary, combo: Combo) -> None:
    pack = merged(library, combo)
    kinds = {k["key"] for k in pack.fact_kinds()}
    for check in pack.checks():
        named = set(applies(check)) | {k for name in KIND_LISTS for k in check.get(name, [])}
        assert named <= kinds, check["key"]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_checks_are_the_pack_rows_as_written(library: packs.PackLibrary, combo: Combo) -> None:
    raw = by_key(raw_items(combo, "specialty", "checks.yaml", "checks"))
    for check in merged(library, combo).checks():
        assert check == raw[check["key"]]


def test_the_launch_abnormal_result_check_is_fully_parameterised(library: packs.PackLibrary) -> None:
    checks = by_key(merged(library, LAUNCH).checks())
    raw = by_key(raw_items(LAUNCH, "specialty", "checks.yaml", "checks"))
    no_action = [c for c in checks.values() if c["rule"] == "no_action_within"]
    assert no_action
    for check in no_action:
        assert check == raw[check["key"]]
        assert check["window_days"] > 0
        assert set(check["flag"]) == {"field", "any_of"} and check["flag"]["any_of"]
        assert check["action_kinds"] and check["label_spans"] is True


def test_the_launch_acknowledgement_check_names_the_acknowledging_field(library: packs.PackLibrary) -> None:
    pack = merged(library, LAUNCH)
    fields = {k["key"]: k["fields"] for k in pack.fact_kinds()}
    checks = [c for c in pack.checks() if c["rule"] == "not_acknowledged_within"]
    assert checks
    for check in checks:
        assert check["window_days"] > 0
        for kind in applies(check):
            assert check["acknowledged_field"] in fields[kind]
        for kind in check["acknowledged_kinds"]:
            assert fields[kind][check["acknowledges_field"]] == "date"


def test_the_launch_reached_clinician_check_uses_a_field_condition(library: packs.PackLibrary) -> None:
    pack = merged(library, LAUNCH)
    fields = {k["key"]: k["fields"] for k in pack.fact_kinds()}
    checks = [c for c in pack.checks() if c["rule"] == "not_reached_clinician"]
    assert checks
    for check in checks:
        assert "reached_kinds" not in check
        assert check["window_days"] > 0 and check["when"]
        for kind in applies(check):
            assert set(check["when"]) <= set(fields[kind]), kind


def test_field_missing_checks_name_a_field_and_may_be_met_by_another_kind(library: packs.PackLibrary) -> None:
    pack = merged(library, LAUNCH)
    fields = {k["key"]: k["fields"] for k in pack.fact_kinds()}
    checks = [c for c in pack.checks() if c["rule"] == "field_missing"]
    assert checks
    for check in checks:
        assert check["or_kinds"] and check["scope"] == "linked"
        assert any(check["field"] in fields[kind] for kind in applies(check))


def test_the_fixture_checks_are_its_own_and_one_is_gentle(library: packs.PackLibrary) -> None:
    fixture = merged(library, FIXTURE).checks()
    assert len(fixture) >= 2
    assert any(c.get("gentle") is True for c in fixture)
    assert {c["key"] for c in fixture}.isdisjoint({c["key"] for c in merged(library, LAUNCH).checks()})


def test_a_check_with_an_unknown_rule_type_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(fixtures / "specialty/zz-specialty/checks.yaml", "checks:\n  - {key: zz_one, rule: zz_rule}\n")
    with pytest.raises(packs.PackError, match="checks.yaml"):
        packs.load_library(fixtures)


def test_a_check_naming_an_unknown_fact_kind_is_refused(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(
        fixtures / "specialty/zz-specialty/checks.yaml",
        "checks:\n  - {key: zz_one, rule: no_action_within, applies_to: session, action_kinds: [zz_nothing]}\n",
    )
    with pytest.raises(packs.PackError, match="zz_nothing"):
        packs.load_library(fixtures)


def test_a_check_flag_may_still_be_a_plain_word_or_a_single_value(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(
        fixtures / "specialty/zz-specialty/checks.yaml",
        "checks:\n"
        "  - {key: zz_word, rule: no_action_within, applies_to: measurement, flag: zz_high, window_days: 3}\n"
        "  - {key: zz_value, rule: no_action_within, applies_to: measurement, window_days: 3,"
        " flag: {field: unit, value: zz}}\n"
        "  - {key: zz_list, rule: no_action_within, applies_to: [measurement, session], window_days: 3}\n",
    )
    checks = by_key(packs.load_library(fixtures).combine(*FIXTURE.codes).checks())
    assert checks["zz_word"]["flag"] == "zz_high"
    assert checks["zz_value"]["flag"] == {"field": "unit", "value": "zz"}
    assert checks["zz_list"]["applies_to"] == ["measurement", "session"]


# --- guideline versions in the overlay ----------------------------------------------------------------------------

FIXTURE_OVERLAY = "overlay/zz-test/zz-specialty"
FIXTURE_GUIDELINES = """\
sources:
  - key: ZZG
    name: Test guideline body
reviewed_at: "2026-10-01"
versions:
  - code: ZZ-G1
    source: ZZG
    title: Test guideline one
    version_label: zz edition 1
    in_force_from: "2020-01-01"
    in_force_to: null
    url: https://zz.invalid/zz-g1
"""


def with_fixture_overlay(fixtures: Path, guidelines: str = FIXTURE_GUIDELINES) -> packs.MergedPack:
    """The fixture packs ship no overlay (so fixture cases stay three layers); tests that need one add it."""
    write(fixtures / FIXTURE_OVERLAY / "pack.yaml", "jurisdiction: zz-test\nspecialty: zz-specialty\nversion: 0.0.1\n")
    write(fixtures / FIXTURE_OVERLAY / "guidelines.yaml", guidelines)
    return packs.load_library(fixtures).combine(*FIXTURE.codes)


def check_guideline_rows(pack: packs.MergedPack, raw: dict[str, Any], combo: Combo) -> None:
    rows = pack.guidelines()
    assert rows and len(rows) == len(raw["versions"])
    for row, written in zip(rows, raw["versions"], strict=True):
        assert {k: row[k] for k in written} == written
        assert (row["jurisdiction"], row["specialty"]) == (combo.jurisdiction, combo.specialty)
        assert row["reviewed_at"] == raw["reviewed_at"]
    sources = {s["key"] for s in pack.guideline_sources()}
    assert sources == {s["key"] for s in raw["sources"]}
    assert {row["source"] for row in rows} <= sources


def test_launch_guideline_versions_come_from_the_overlay_with_their_codes(library: packs.PackLibrary) -> None:
    check_guideline_rows(merged(library, LAUNCH), LAUNCH.raw("overlay", "guidelines.yaml"), LAUNCH)


def test_fixture_guideline_versions_come_from_an_overlay_with_their_codes(tree: tuple[Path, Path]) -> None:
    check_guideline_rows(with_fixture_overlay(tree[1]), yaml.safe_load(FIXTURE_GUIDELINES), FIXTURE)


def test_launch_guideline_versions_cover_the_priority_guidelines(library: packs.PackLibrary) -> None:
    raw = LAUNCH.raw("overlay", "guidelines.yaml")
    codes = {row["code"] for row in merged(library, LAUNCH).guidelines()}
    assert set(raw["priority_guidelines"]) <= codes


def test_launch_and_fixture_guidelines_differ(library: packs.PackLibrary, tree: tuple[Path, Path]) -> None:
    launch = {r["code"] for r in merged(library, LAUNCH).guidelines()}
    fixture = {r["code"] for r in with_fixture_overlay(tree[1]).guidelines()}
    assert fixture and launch.isdisjoint(fixture)


def test_a_guideline_version_cannot_carry_guidance_text(tree: tuple[Path, Path]) -> None:
    """Labels, dates and links only (decision 0005)."""
    text = FIXTURE_GUIDELINES.replace("version_label:", "text: zz guidance words\n    version_label:", 1)
    with pytest.raises(packs.PackError, match="guidelines.yaml"):
        with_fixture_overlay(tree[1], text)


def test_a_guideline_version_naming_an_unknown_source_is_refused(tree: tuple[Path, Path]) -> None:
    with pytest.raises(packs.PackError, match="ZZ-NOWHERE"):
        with_fixture_overlay(tree[1], FIXTURE_GUIDELINES.replace("source: ZZG", "source: ZZ-NOWHERE", 1))


def test_a_combination_without_an_overlay_has_no_guidelines(library: packs.PackLibrary) -> None:
    pack = merged(library, FIXTURE)
    assert "overlay" not in pack.versions
    assert pack.guidelines() == [] and pack.guideline_sources() == []


# --- own-record sources and kinds that arrive from outside ---------------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_own_record_sources_are_the_ones_the_pack_marks(library: packs.PackLibrary, combo: Combo) -> None:
    raw = raw_items(combo, "specialty", "sources.yaml", "source_types")
    marked = frozenset(s["key"] for s in raw if s.get("own_record") is True)
    assert marked
    assert merged(library, combo).own_record_sources() == marked
    assert marked < {s["key"] for s in raw}


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_arriving_kinds_are_the_fact_kinds_the_pack_marks(library: packs.PackLibrary, combo: Combo) -> None:
    raw = raw_items(combo, "specialty", "facts.yaml", "fact_kinds")
    marked = frozenset(k["key"] for k in raw if k.get("arrives") is True)
    assert marked
    assert merged(library, combo).arriving_kinds() == marked
    assert marked < {k["key"] for k in raw}


def test_launch_and_fixture_mark_different_sources_and_kinds(library: packs.PackLibrary) -> None:
    launch, fixture = merged(library, LAUNCH), merged(library, FIXTURE)
    assert launch.own_record_sources() != fixture.own_record_sources()
    assert launch.arriving_kinds() != fixture.arriving_kinds()


def test_a_disabled_own_record_source_is_not_one(tree: tuple[Path, Path]) -> None:
    _, fixtures = tree
    write(fixtures / FIXTURE_OVERLAY / "pack.yaml", "jurisdiction: zz-test\nspecialty: zz-specialty\nversion: 0.0.1\n")
    write(
        fixtures / FIXTURE_OVERLAY / "sources.yaml",
        "source_types:\n  - {key: clinic_notes, enabled: false}\n",
    )
    assert "clinic_notes" not in packs.load_library(fixtures).combine(*FIXTURE.codes).own_record_sources()
