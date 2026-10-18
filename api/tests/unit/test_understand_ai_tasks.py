"""Slice D's AI tasks (architecture 6.3): T-11 links facts to questions, T-13a labels the spans a check
points at. Both only suggest: the expert confirms every link and decides every finding (rule 1).

- Output schemas hold ids, a fixed label list or a boolean, word ids and a short `reason` only.
- Only pairs the code offered are kept: a fact or question the model names that was not a
  candidate is dropped, and a label citing words outside its candidate's span is dropped.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest

from app.core.packs import load_library
from app.features.understand import ai_tasks
from tests.support.packs import BOTH, FIXTURE_ROOT, LAUNCH_ROOT, Combo

Q1, Q2 = uuid.uuid4(), uuid.uuid4()
F1, F2, F3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _strings(schema: Mapping[str, Any]) -> set[str]:
    """Every free string property, anywhere in the schema."""
    found: set[str] = set()
    for name, prop in schema.get("properties", {}).items():
        if prop.get("type") == "string" and "enum" not in prop:
            found.add(name)
        found |= _strings(prop)
        found |= _strings(prop.get("items", {}))
    return found


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_t11_schema_holds_ids_a_yes_no_and_a_short_reason(combo: Combo) -> None:
    task = ai_tasks.t11(load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*combo.codes))
    item = task.schema["properties"]["links"]["items"]
    assert set(item["properties"]) == {"fact_id", "question_id", "relevant", "reason"}
    assert item["properties"]["relevant"]["type"] == "boolean"
    assert item["properties"]["reason"]["maxLength"] <= 80
    assert _strings(task.schema) == {"fact_id", "question_id", "reason"}
    assert task.task_id == "T-11" and task.prompt_version


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_t13a_schema_is_a_fixed_label_and_the_words_it_rests_on(combo: Combo) -> None:
    task = ai_tasks.t13a(load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*combo.codes))
    item = task.schema["properties"]["labels"]["items"]
    assert set(item["properties"]) == {"fact_id", "label", "word_ids"}
    assert item["properties"]["label"]["enum"] == ["action_taken", "no_action", "safety_netting", "none"]
    assert _strings(task.schema) == {"fact_id"}
    assert task.task_id == "T-13a" and task.prompt_version


def test_t11_sees_each_question_and_candidate_on_its_own_line() -> None:
    text = ai_tasks.t11_text(
        [(Q1, "Was the  result\nacted on?")],
        [(F1, "result", "2024-02-14", "ferritin 9"), (F2, "letter", None, None)],
    )
    assert text.splitlines() == [
        f"question\t{Q1}\tWas the result acted on?",
        f"fact\t{F1}\tresult\t2024-02-14\tferritin 9",
        f"fact\t{F2}\tletter\t\t",
    ]


def test_t11_keeps_only_relevant_pairs_the_code_offered() -> None:
    offered = {Q1: {F1, F2}, Q2: {F3}}
    out = {
        "links": [
            {"fact_id": str(F1), "question_id": str(Q1), "relevant": True, "reason": "same test"},
            {"fact_id": str(F2), "question_id": str(Q1), "relevant": False, "reason": "unrelated"},
            {"fact_id": str(F3), "question_id": str(Q1), "relevant": True, "reason": "not offered here"},
            {"fact_id": "not-a-uuid", "question_id": str(Q2), "relevant": True, "reason": "x"},
            {"fact_id": str(F1), "question_id": str(Q1), "relevant": True, "reason": "repeat"},
        ]
    }
    assert ai_tasks.accept_links(out, offered) == [(F1, Q1, "same test")]


def test_t13a_sees_the_rule_and_each_candidate_s_words_with_their_ids() -> None:
    text = ai_tasks.t13a_text("no_action_within", [(F1, [("w1", "Start"), ("w2", "iron")])])
    assert text.splitlines() == ["rule\tno_action_within", f"fact\t{F1}", "w1 Start", "w2 iron"]


def test_t13a_keeps_labels_whose_words_lie_inside_their_candidate() -> None:
    spans = {F1: {"w1", "w2"}, F2: {"w9"}}
    out = {
        "labels": [
            {"fact_id": str(F1), "label": "action_taken", "word_ids": ["w1", "w2"]},
            {"fact_id": str(F2), "label": "action_taken", "word_ids": ["w1"]},
            {"fact_id": str(F3), "label": "action_taken", "word_ids": ["w1"]},
            {"fact_id": str(F1), "label": "none", "word_ids": ["w1"]},
        ]
    }
    assert ai_tasks.accept_labels(out, spans) == {F1: "action_taken"}
