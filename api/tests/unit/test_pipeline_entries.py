"""Slice A: T-07b page-level `entries[]` (ADR 0028): the schema has no free text, provenance refuses
entries whose word ids are unknown, out of order or overlapping, and a rejection is counted, never logged
with text. Runs under the launch and the fixture packs."""

from __future__ import annotations

import logging
from typing import Any

import jsonschema_rs
import pytest

from app.core.packs import MergedPack, default_library
from app.features.pipeline import extract
from tests.support.packs import BOTH, Combo

WORDS = [(f"p1w{n}", f"word{n}") for n in range(1, 11)]
POSITIONS = {w: n for n, (w, _) in enumerate(WORDS, start=1)}


def pack_of(combo: Combo) -> MergedPack:
    return default_library().combine(*combo.codes)


def entry(pack: MergedPack, first: str, last: str, **over: Any) -> dict[str, Any]:
    base = {
        "kind": pack.entry_kinds()[0]["key"],
        "first_word_id": first,
        "last_word_id": last,
        "author_role": None,
        "specialty": None,
    }
    return {**base, **over}


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_schema_has_entries_built_from_the_pack_and_no_free_text(combo: Combo) -> None:
    pack = pack_of(combo)
    schema = extract.output_schema(pack)
    assert "entries" in schema["required"]
    item = schema["properties"]["entries"]["items"]
    assert set(item["properties"]) == {"kind", "first_word_id", "last_word_id", "author_role", "specialty"}
    assert item["additionalProperties"] is False
    assert item["properties"]["kind"]["enum"] == [k["key"] for k in pack.entry_kinds()]
    assert set(item["properties"]["author_role"]["enum"]) == {*(r["key"] for r in pack.author_roles()), None}
    assert set(item["properties"]["specialty"]["enum"]) == {*(s["key"] for s in pack.entry_specialties()), None}
    for name in ("first_word_id", "last_word_id"):
        assert item["properties"][name]["maxLength"] <= 40
    assert "clinician_role" in schema["properties"]["facts"]["items"]["properties"], "clinician_role is kept"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_validator_refuses_free_text_in_an_entry(combo: Combo) -> None:
    pack = pack_of(combo)
    validator = jsonschema_rs.validator_for(extract.output_schema(pack))
    page: dict[str, Any] = {
        "source_type": None,
        "printed_dates": [],
        "facts": [],
        "references_out": [],
        "availability_evidence": [],
        "actions_requested_of_clinician": [],
    }
    good = entry(pack, "p1w1", "p1w2")
    assert validator.is_valid({**page, "entries": [good]})
    assert not validator.is_valid({**page, "entries": [{**good, "summary": "a short prose summary"}]})
    assert not validator.is_valid({**page, "entries": [{**good, "kind": "not a pack kind"}]})
    assert not validator.is_valid({**page, "entries": [{**good, "author_role": "free text role"}]})
    assert not validator.is_valid({**page, "entries": [{**good, "first_word_id": "x" * 500}]})


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_good_entries_are_kept_with_their_word_numbers(combo: Combo) -> None:
    pack = pack_of(combo)
    role = pack.author_roles()[0]["key"]
    out = {"entries": [entry(pack, "p1w1", "p1w3", author_role=role), entry(pack, "p1w4", "p1w4")]}
    result = extract.sort_entries(out, POSITIONS, pack)
    assert result.rejected == 0
    assert [(e.first_n, e.last_n, e.author_role) for e in result.kept] == [(1, 3, role), (4, 4, None)]
    assert result.kept[0].first_word_id == "p1w1" and result.kept[0].last_word_id == "p1w3"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_unknown_reversed_and_overlapping_entries_are_dropped_and_counted(combo: Combo) -> None:
    pack = pack_of(combo)
    out = {
        "entries": [
            entry(pack, "p1w1", "p1w3"),
            entry(pack, "p1w2", "p1w5"),  # overlaps the first
            entry(pack, "p1w9", "p1w6"),  # last before first
            entry(pack, "p1w1", "p9w9"),  # unknown id
            entry(pack, "p1w4", "p1w5"),
            entry(pack, "p1w5", "p1w6"),  # overlaps the one before
        ]
    }
    result = extract.sort_entries(out, POSITIONS, pack)
    assert [(e.first_n, e.last_n) for e in result.kept] == [(1, 3), (4, 5)]
    assert result.rejected == 4


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_kind_or_role_outside_the_pack_is_dropped(combo: Combo) -> None:
    pack = pack_of(combo)
    out = {"entries": [entry(pack, "p1w1", "p1w2", kind="nope"), entry(pack, "p1w3", "p1w4", specialty="nope")]}
    result = extract.sort_entries(out, POSITIONS, pack)
    assert result.kept == [] and result.rejected == 2


def test_no_answer_means_no_entries() -> None:
    pack = pack_of(BOTH[0])
    assert extract.sort_entries(None, POSITIONS, pack).kept == []
    assert extract.sort_entries({}, POSITIONS, pack).rejected == 0


def test_a_rejection_logs_no_word_text(caplog: pytest.LogCaptureFixture) -> None:
    pack = pack_of(BOTH[0])
    with caplog.at_level(logging.DEBUG):
        extract.sort_entries({"entries": [entry(pack, "p1w1", "p9w9")]}, POSITIONS, pack)
    assert not any(text in caplog.text for _, text in WORDS)
