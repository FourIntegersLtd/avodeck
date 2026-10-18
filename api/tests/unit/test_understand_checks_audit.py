"""Slice D audit: `label_spans` on every rule that looks for something done after, the planted eval items
(evals/README.md 11-13) through the launch checks, and comments that never leave slice D."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.core import packs
from app.features.understand.checks.rules import CheckFact, Hit, evaluate, to_label
from tests.support.packs import FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT

REPO = Path(__file__).resolve().parents[3]
EXPECTED = REPO / "evals" / "gb-eaw" / "clin-neg-breach" / "general-practice" / "expected"


def _fact(kind: str, event: date | None, available: date | None = None, **fields: Any) -> CheckFact:
    return CheckFact(uuid.uuid4(), uuid.uuid4(), kind, fields, None, event, available)


def _launch() -> Any:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*LAUNCH.codes)


# --- label_spans on actions_not_done / not_followed_up ---------------------------------------------------

ASKS = {
    "key": "letter_actions",
    "rule": "actions_not_done",
    "applies_to": "letter",
    "field": "actions_requested",
    "action_kinds": ["task"],
    "window_days": 28,
    "label_spans": True,
}


@pytest.mark.parametrize("check", [ASKS, {**ASKS, "rule": "not_followed_up", "followup_kinds": ["task"]}])
def test_with_labelled_spans_only_an_action_taken_label_counts_after_a_request(check: dict[str, Any]) -> None:
    letter = _fact("letter", date(2024, 4, 1), date(2024, 4, 5), actions_requested="repeat bloods")
    task = _fact("task", date(2024, 4, 20))
    assert to_label(check, [letter, task]) == [task.fact_id]
    assert len(evaluate(check, [letter, task])) == 1, "an entry nobody labelled is not an action"
    assert len(evaluate(check, [letter, task], labels={task.fact_id: "no_action"})) == 1
    assert evaluate(check, [letter, task], labels={task.fact_id: "action_taken"}) == []


def test_the_launch_letter_check_asks_for_labels_and_honours_them() -> None:
    check = next(c for c in _launch().checks() if c["rule"] == "actions_not_done" and c.get("label_spans"))
    kind = check["applies_to"] if isinstance(check["applies_to"], str) else check["applies_to"][0]
    letter = _fact(kind, date(2024, 4, 1), date(2024, 4, 5), **{check["field"]: "zz"})
    later = _fact(check["action_kinds"][0], date(2024, 4, 9))
    assert to_label(check, [letter, later]) == [later.fact_id]
    assert len(evaluate(check, [letter, later])) == 1
    assert evaluate(check, [letter, later], labels={later.fact_id: "action_taken"}) == []


# --- the planted eval items through the launch checks ---------------------------------------------------


def _planted_facts() -> tuple[list[CheckFact], dict[uuid.UUID, int]]:
    marks: dict[int, date] = {}
    for mark in json.loads((EXPECTED / "availability.json").read_text()):
        day = date.fromisoformat(mark["date"])
        marks[mark["page"]] = min(day, marks.get(mark["page"], day))
    pages: dict[int, uuid.UUID] = {}
    page_of: dict[uuid.UUID, int] = {}
    facts = []
    for row in json.loads((EXPECTED / "facts.json").read_text()):
        page = pages.setdefault(row["page"], uuid.uuid4())
        page_of[page] = row["page"]
        event = date.fromisoformat(row["event_date"]) if row.get("event_date") else None
        fields = {k: v for k, v in (row.get("fields") or {}).items() if v is not None}
        facts.append(CheckFact(uuid.uuid4(), page, row["kind"], fields, row["value"], event, marks.get(row["page"])))
    return facts, page_of


def _hits(key: str) -> tuple[list[Hit], dict[uuid.UUID, int]]:
    facts, page_of = _planted_facts()
    check = next(c for c in _launch().checks() if c["key"] == key)
    by_id = {f.fact_id: page_of[f.page_id] for f in facts}
    return evaluate(check, facts), by_id


def test_planted_11_the_low_ferritin_with_nothing_done_in_14_days_is_found_on_both_pages() -> None:
    hits, page = _hits("abnormal_result_no_action")
    on_day = [h for h in hits if {119, 402} <= {page[f] for f in h.evidence}]
    assert on_day, "the abnormal set of 14 Feb, with the lab's own copies on page 402 (and any later copies)"


def test_planted_12_the_referral_acknowledged_after_six_weeks_is_found_citing_both_pages() -> None:
    hits, page = _hits("referral_acknowledged")
    assert any(sorted(page[f] for f in h.evidence) == [2, 117] for h in hits)


def test_planted_13_the_out_of_hours_visit_passed_on_next_day_is_not_found() -> None:
    hits, page = _hits("ooh_reached_clinician")
    assert all(111 not in {page[f] for f in h.evidence} for h in hits)


# --- comments never go into a report -----------------------------------------------------------------------


def test_comments_are_read_only_by_slice_d_never_by_the_report() -> None:
    """Comments are notes between the team (architecture 10.4): no other slice reads the table."""
    allowed = {"app/core/contracts/case_tables.py"}
    found = []
    for path in (REPO / "api" / "app").rglob("*.py"):
        rel = path.relative_to(REPO / "api").as_posix()
        if rel.startswith("app/features/understand/") or rel in allowed:
            continue
        if re.search(r"app\.comments\b|[\"']comments[\"']", path.read_text()):
            found.append(rel)
    assert found == []
