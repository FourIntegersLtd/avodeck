"""Slice A: the letters eval names each letter's author specialty and body span (item 6 of the plan).

Ten synthetic letters (typed, scanned, faxed, handwritten). The expected specialty is one of the general-practice
pack's specialties, and the `letter` fact kind has a `specialty` field to hold it.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
EVAL = ROOT / "evals/gb-eaw/clin-neg-breach/general-practice/expected/letters.json"
PACK = ROOT / "packs/specialty/general-practice"


def test_ten_letters_cover_every_kind_of_copy_with_known_specialties() -> None:
    letters = json.loads(EVAL.read_text())
    keys = {s["key"] for s in yaml.safe_load((PACK / "specialties.yaml").read_text())["specialties"]}
    assert len(letters) == 10
    assert {ln["copy"] for ln in letters} == {"typed", "scanned", "faxed", "handwritten"}
    assert all(ln["author_specialty"] in keys for ln in letters)
    assert all(0 < ln["body_first_n"] <= ln["body_last_n"] for ln in letters)
    assert len({ln["letter"] for ln in letters}) == 10


def test_the_letter_kind_holds_the_authors_specialty() -> None:
    kinds = {k["key"]: k for k in yaml.safe_load((PACK / "facts.yaml").read_text())["fact_kinds"]}
    assert kinds["letter"]["fields"]["specialty"] == "string"
    assert "specialty" in kinds["letter"]["field_labels"]
