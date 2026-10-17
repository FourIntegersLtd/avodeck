"""The generator's model recordings answer as the launch packs ask (cross-slice request A -> 0).

T-07b answers name a page's source with one of the specialty pack's source keys, never the
generator's own document type, so no test has to translate them. Every entry in the record series
that holds the planted gap (architecture 7, planted item 2) carries the date printed beside it, so
the pipeline can see the series stop. Read from the committed expected files and the committed
offline recordings; no source or fact word is written here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from tests.support.packs import REPO

EVAL = REPO / "evals" / "gb-eaw" / "clin-neg-breach" / "general-practice"
ANSWERS = REPO / "api" / "tests" / "fixtures" / "ai" / "pipeline" / "answer"
SOURCES = REPO / "packs" / "specialty" / "general-practice" / "sources.yaml"


def expected(name: str) -> Any:
    return json.loads((EVAL / "expected" / name).read_text())


def recorded_page_answers() -> list[dict[str, Any]]:
    out = []
    for path in sorted(ANSWERS.glob("t07b-*.json")):
        for answer in json.loads(Path(path).read_text())["response"]["responses"]:
            out.append(answer["output"])
    return out


def gap_kind() -> str:
    (planted,) = expected("gaps.json")
    return str(planted["fact_kind"])


def test_every_recorded_page_names_a_source_key_from_the_pack() -> None:
    keys = {entry["key"] for entry in yaml.safe_load(SOURCES.read_text())["source_types"]}
    named = {answer["source_type"] for answer in recorded_page_answers()}
    assert named, "no recorded T-07b answers"
    assert named <= keys, f"not pack source keys: {sorted(named - keys)}"


def test_every_recorded_source_key_is_the_expected_one_for_its_page() -> None:
    wanted = {int(row["page"]): row["source_type"] for row in expected("sources.json")}
    for path in sorted(ANSWERS.glob("t07b-*.json")):
        page = int(path.stem.rsplit("-p", 1)[1])
        for answer in json.loads(path.read_text())["response"]["responses"]:
            assert answer["output"]["source_type"] == wanted[page], f"page {page}"


def test_every_entry_in_the_gap_series_has_its_printed_date() -> None:
    entries = [f for f in expected("facts.json") if f["kind"] == gap_kind()]
    assert entries, "the planted gap names no fact kind in the bundle"
    undated = sorted({int(f["page"]) for f in entries if not f["event_date"]})
    assert not undated, f"entries without a date on pages {undated}"


def test_the_offline_recordings_hold_a_dated_page_of_the_gap_series() -> None:
    kind = gap_kind()
    entries = [f for answer in recorded_page_answers() for f in answer["facts"] if f["kind"] == kind]
    assert len(entries) >= 3, "no page of the gap series is in the offline recordings"
    assert all(f["fields"].get("date") for f in entries), "a recorded entry has no date"


def test_handwriting_does_not_depend_on_where_the_repository_is_checked_out() -> None:
    """The bundle is regenerated in worktrees and on CI; a writer's hand must come out the same."""
    import importlib.util
    import sys

    generator = REPO / "evals" / "generator"
    sys.path.insert(0, str(generator))
    try:
        spec = importlib.util.spec_from_file_location("eval_hand", generator / "hand.py")
        assert spec and spec.loader
        hand = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hand)
    finally:
        sys.path.remove(str(generator))
    font = hand.SCRAWL[0]
    here = hand.writer_for(f"/one/checkout/fonts/{font}", 0.7, (30, 35, 80))
    there = hand.writer_for(f"/another/checkout/fonts/{font}", 0.7, (30, 35, 80))
    assert (here.slant, here.size, here.spacing, here.climb) == (there.slant, there.size, there.spacing, there.climb)
