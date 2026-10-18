"""Slice D's check rule types: the code side of "Things to look at" (docs/slices/D-understand.md).

Rule types are written once here; packs only choose and parameterise them (`pack.checks()`). A
finding is a pointer for the expert to look at, never a conclusion: each one names the facts it
rests on. Cases are calibrated on the eval bundle (`evals/.../expected/findings.json`).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest

from app.core import packs
from app.features.understand.checks.rules import RULE_TYPES, CheckFact, UnknownRule, evaluate, to_label
from tests.support.packs import BOTH, FIXTURE_ROOT, LAUNCH_ROOT, Combo


def _fact(
    kind: str,
    event: date | None,
    available: date | None = None,
    value: str | None = None,
    linked: bool = False,
    **fields: Any,
) -> CheckFact:
    return CheckFact(
        fact_id=uuid.uuid4(),
        page_id=uuid.uuid4(),
        kind=kind,
        fields=fields,
        value=value,
        event_date=event,
        available_at=available,
        linked=linked,
    )


def _ids(*facts: CheckFact) -> tuple[uuid.UUID, ...]:
    return tuple(f.fact_id for f in facts)


# --- every rule a pack names exists -----------------------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_every_rule_type_a_pack_names_is_implemented(combo: Combo) -> None:
    pack = packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*combo.codes)
    assert {c["rule"] for c in pack.checks()} <= RULE_TYPES


def test_an_unknown_rule_type_is_refused() -> None:
    with pytest.raises(UnknownRule):
        evaluate({"key": "x", "rule": "made_up"}, [])


# --- no_action_within -------------------------------------------------------------------------------

ABNORMAL: Mapping[str, Any] = {
    "key": "abnormal_no_action",
    "rule": "no_action_within",
    "applies_to": "result",
    "flag": {"field": "flag", "any_of": ["L", "H"]},
    "window_days": 14,
    "action_kinds": ["prescription", "task", "consultation"],
}


def test_an_abnormal_result_with_nothing_done_fires_with_its_copies_as_evidence() -> None:
    """Ferritin 9 (L) filed 16 Feb; the lab's own copy on another page has no flag but the same value."""
    low = _fact("result", date(2024, 2, 14), date(2024, 2, 16), value="9", flag="L")
    copy = _fact("result", date(2024, 2, 14), None, value="9", flag="")
    normal = _fact("result", date(2024, 2, 14), date(2024, 2, 16), value="140", flag="")
    hits = evaluate(ABNORMAL, [low, copy, normal])
    assert len(hits) == 1
    assert hits[0].check_key == "abnormal_no_action"
    assert set(hits[0].evidence) == set(_ids(low, copy))


def test_an_action_inside_the_window_from_filing_stops_it() -> None:
    low = _fact("result", date(2024, 2, 14), date(2024, 2, 16), value="9", flag="L")
    script = _fact("prescription", date(2024, 2, 29))
    assert evaluate(ABNORMAL, [low, script]) == []


def test_an_action_after_the_window_does_not_count() -> None:
    low = _fact("result", date(2024, 2, 14), date(2024, 2, 16), value="9", flag="L")
    late = _fact("prescription", date(2024, 3, 2))
    assert len(evaluate(ABNORMAL, [low, late])) == 1


def test_with_labelled_spans_only_an_action_taken_label_stops_it() -> None:
    check = {**ABNORMAL, "label_spans": True}
    low = _fact("result", date(2024, 2, 14), date(2024, 2, 16), value="9", flag="L")
    visit = _fact("consultation", date(2024, 2, 20))
    assert to_label(check, [low, visit]) == [visit.fact_id]
    assert len(evaluate(check, [low, visit], labels={visit.fact_id: "no_action"})) == 1
    assert evaluate(check, [low, visit], labels={visit.fact_id: "action_taken"}) == []
    assert to_label(ABNORMAL, [low, visit]) == [], "nothing is labelled unless the pack asks"


def test_the_flag_can_be_a_single_value() -> None:
    check = {**ABNORMAL, "flag": {"field": "flag", "value": "H"}}
    assert evaluate(check, [_fact("result", date(2024, 2, 14), value="9", flag="L")]) == []
    assert len(evaluate(check, [_fact("result", date(2024, 2, 14), value="9", flag="H")])) == 1


# --- not_acknowledged_within ------------------------------------------------------------------------

ACK: Mapping[str, Any] = {
    "key": "referral_ack",
    "rule": "not_acknowledged_within",
    "applies_to": "referral",
    "window_days": 42,
    "acknowledged_kinds": ["letter"],
    "acknowledges_field": "acknowledges_referral_dated",
    "acknowledged_field": "acknowledged_on",
}


def test_a_referral_acknowledged_late_fires_and_cites_both() -> None:
    referral = _fact("referral", date(2024, 6, 24))
    reply = _fact("letter", date(2024, 8, 30), acknowledges_referral_dated="2024-06-24")
    hits = evaluate(ACK, [referral, reply])
    assert [h.evidence for h in hits] == [_ids(referral, reply)]


def test_a_referral_acknowledged_in_time_does_not_fire() -> None:
    referral = _fact("referral", date(2024, 6, 24))
    reply = _fact("letter", date(2024, 7, 10), acknowledges_referral_dated="2024-06-24")
    assert evaluate(ACK, [referral, reply]) == []


def test_the_referral_s_own_acknowledged_date_counts() -> None:
    assert evaluate(ACK, [_fact("referral", date(2024, 6, 24), acknowledged_on="2024-07-01")]) == []


def test_a_referral_never_acknowledged_fires_on_its_own() -> None:
    referral = _fact("referral", date(2024, 6, 24))
    other = _fact("letter", date(2024, 7, 1), acknowledges_referral_dated="2024-01-02")
    assert [h.evidence for h in evaluate(ACK, [referral, other])] == [_ids(referral)]


# --- not_reached_clinician --------------------------------------------------------------------------

REACHED: Mapping[str, Any] = {
    "key": "after_hours_reached",
    "rule": "not_reached_clinician",
    "applies_to": ["consultation"],
    "when": {"setting": "out_of_hours"},
    "window_days": 2,
}


def test_an_after_hours_contact_passed_on_next_day_does_not_fire() -> None:
    contact = _fact("consultation", date(2024, 3, 30), date(2024, 3, 31), setting="out_of_hours")
    assert evaluate(REACHED, [contact]) == []


def test_a_contact_passed_on_late_or_never_fires_and_others_are_ignored() -> None:
    late = _fact("consultation", date(2024, 3, 30), date(2024, 4, 5), setting="out_of_hours")
    never = _fact("consultation", date(2024, 3, 30), None, setting="out_of_hours")
    surgery = _fact("consultation", date(2024, 3, 30), None, setting="surgery")
    hits = evaluate(REACHED, [late, never, surgery])
    assert sorted(h.evidence for h in hits) == sorted([_ids(late), _ids(never)])


# --- field_missing ----------------------------------------------------------------------------------

MISSING: Mapping[str, Any] = {
    "key": "examination_recorded",
    "rule": "field_missing",
    "applies_to": "consultation",
    "field": "examination",
    "or_kinds": ["examination"],
    "scope": "linked",
    "gentle": True,
}


def test_a_linked_visit_with_no_examination_fires_gently() -> None:
    visit = _fact("consultation", date(2024, 5, 3), linked=True)
    hits = evaluate(MISSING, [visit])
    assert [h.evidence for h in hits] == [_ids(visit)]
    assert hits[0].gentle is True


def test_an_examination_recorded_the_same_day_or_in_the_field_counts() -> None:
    visit = _fact("consultation", date(2024, 5, 3), linked=True)
    exam = _fact("examination", date(2024, 5, 3))
    assert evaluate(MISSING, [visit, exam]) == []
    assert evaluate(MISSING, [_fact("consultation", date(2024, 5, 3), linked=True, examination="chest clear")]) == []


def test_linked_scope_skips_visits_not_tied_to_a_question() -> None:
    assert evaluate(MISSING, [_fact("consultation", date(2024, 5, 3))]) == []
    everyone = {**MISSING, "scope": "all"}
    assert len(evaluate(everyone, [_fact("consultation", date(2024, 5, 3))])) == 1
    assert evaluate(everyone, [_fact("consultation", date(2024, 5, 3))])[0].gentle is True


# --- actions_not_done, not_followed_up, monitoring_overdue ------------------------------------------


def test_a_letter_asking_for_action_with_nothing_after_it_fires() -> None:
    check = {
        "key": "letter_actions",
        "rule": "actions_not_done",
        "applies_to": "letter",
        "field": "actions_requested",
        "action_kinds": ["task", "prescription"],
        "window_days": 28,
    }
    asks = _fact("letter", date(2024, 4, 1), date(2024, 4, 5), actions_requested="repeat bloods")
    silent = _fact("letter", date(2024, 4, 1), date(2024, 4, 5))
    assert [h.evidence for h in evaluate(check, [asks, silent])] == [_ids(asks)]
    done = _fact("task", date(2024, 4, 20))
    assert evaluate(check, [asks, done]) == []


def test_a_missed_appointment_with_no_follow_up_fires() -> None:
    check = {
        "key": "missed_followed_up",
        "rule": "not_followed_up",
        "applies_to": "missed_appointment",
        "window_days": 14,
        "followup_kinds": ["consultation", "task"],
    }
    missed = _fact("missed_appointment", date(2024, 1, 10))
    assert len(evaluate(check, [missed])) == 1
    assert evaluate(check, [missed, _fact("task", date(2024, 1, 12))]) == []
    assert len(evaluate(check, [missed, _fact("task", date(2024, 2, 1))])) == 1


def test_a_repeat_medicine_without_its_monitoring_test_fires() -> None:
    check = {
        "key": "monitoring",
        "rule": "monitoring_overdue",
        "applies_to": "prescription",
        "table": "drug_monitoring",
    }
    tables = {"drug_monitoring": [{"drug": "Drug A", "tests": ["Test X"], "interval_days": 90}]}
    script = _fact("prescription", date(2024, 6, 1), drug="drug a")
    recent = _fact("result", date(2024, 4, 1), test="test x")
    stale = _fact("result", date(2023, 12, 1), test="Test X")
    unlisted = _fact("prescription", date(2024, 6, 1), drug="Drug B")
    assert evaluate(check, [script, recent, unlisted], tables=tables) == []
    assert [h.evidence for h in evaluate(check, [script, stale], tables=tables)] == [_ids(script, stale)]
    assert [h.evidence for h in evaluate(check, [script], tables=tables)] == [_ids(script)]
    assert evaluate(check, [script], tables={}) == [], "an empty or missing table checks nothing"


def test_rejected_facts_never_take_part() -> None:
    low = _fact("result", date(2024, 2, 14), date(2024, 2, 16), value="9", flag="L")
    rejected = CheckFact(**{**low.__dict__, "fact_id": uuid.uuid4(), "status": "rejected"})
    assert evaluate(ABNORMAL, [rejected]) == []
