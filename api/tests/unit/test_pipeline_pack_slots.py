"""Slice A pack slots: wording and numbers the pipeline reads from the packs (rule 11).

- `quarantine_note(pages=...)` and `translation_request(pages=...)`: the paragraphs the draft to the
  instructing party carries for set-aside pages and for pages that need a translation.
- `record_gap_rules()`: the thresholds for where a record source goes quiet (specialty pack).
- `quarantine_purge_days` is on the pack data the web gets (`CasePack`), so S30 shows the real delay.
Each runs under the launch packs and the fixture packs; expected values are read from the pack files.
"""

from __future__ import annotations

import pytest

from app.core import packs
from app.core.routes.packs import case_pack
from app.features.pipeline import gaps
from tests.support.packs import BOTH, FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo

RULE_KEYS = {
    "header_min_pages",
    "source_end_min_pages",
    "list_min_facts",
    "list_share",
    "regular_months",
    "recent_months",
    "gap_min_months",
}


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_quarantine_note_is_the_packs_wording_with_the_pages_filled(
    library: packs.PackLibrary, combo: Combo
) -> None:
    pack = library.combine(*combo.codes)
    raw = combo.raw("jurisdiction", "vocabulary.yaml")["request_notes"]["quarantine"]
    note = pack.quarantine_note(pages="3, 5")
    assert "3, 5" in note and "{" not in note
    assert note == raw.replace("{pages}", "3, 5").replace(
        "{instructing_party}", pack.instructing_party_types()[0]["label"]
    )


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_translation_request_is_the_packs_wording_with_the_pages_filled(
    library: packs.PackLibrary, combo: Combo
) -> None:
    pack = library.combine(*combo.codes)
    raw = combo.raw("jurisdiction", "vocabulary.yaml")["request_notes"]["translation"]
    text = pack.translation_request(pages="7")
    assert "7" in text and "{" not in text
    assert text == raw.replace("{pages}", "7").replace(
        "{instructing_party}", pack.instructing_party_types()[0]["label"]
    )


def test_the_wording_differs_between_launch_and_fixture_packs(library: packs.PackLibrary) -> None:
    launch, fixture = library.combine(*LAUNCH.codes), library.combine(*FIXTURE.codes)
    assert launch.quarantine_note(pages="1") != fixture.quarantine_note(pages="1")
    assert launch.translation_request(pages="1") != fixture.translation_request(pages="1")


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_note_with_no_pages_slot_value_is_refused(library: packs.PackLibrary, combo: Combo) -> None:
    pack = library.combine(*combo.codes)
    with pytest.raises(packs.PackError, match="pages"):
        pack.quarantine_note()
    with pytest.raises(packs.PackError, match="pages"):
        pack.translation_request()


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_record_gap_rules_come_from_the_specialty_pack(library: packs.PackLibrary, combo: Combo) -> None:
    pack = library.combine(*combo.codes)
    rules = pack.record_gap_rules()
    assert set(rules) == RULE_KEYS
    assert rules == combo.raw("specialty", "pack.yaml")["record_gaps"]
    assert gaps.Rules.from_pack(pack) == gaps.Rules(**rules)


def test_the_record_gap_rules_differ_between_launch_and_fixture_packs(library: packs.PackLibrary) -> None:
    assert library.combine(*LAUNCH.codes).record_gap_rules() != library.combine(*FIXTURE.codes).record_gap_rules()


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_case_pack_carries_the_quarantine_delay(library: packs.PackLibrary, combo: Combo) -> None:
    data = case_pack(library.combine(*combo.codes))
    assert data.quarantine_purge_days == combo.raw("jurisdiction", "pack.yaml")["quarantine_purge_days"]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_extension_request_is_the_packs_wording_and_day_count(library: packs.PackLibrary, combo: Combo) -> None:
    pack = library.combine(*combo.codes)
    raw = combo.raw("jurisdiction", "vocabulary.yaml")["request_extension"]
    assert pack.request_extension_days() == raw["days"]
    text = pack.extension_request(days=str(raw["days"]))
    assert str(raw["days"]) in text and "{" not in text
    assert text == raw["text"].replace("{days}", str(raw["days"])).replace(
        "{instructing_party}", pack.instructing_party_types()[0]["label"]
    )
