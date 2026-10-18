"""Rule 1 for slice G's AI tasks (T-20a/b, T-21a-e): they find words, ids and short labels, never opinion.

Every string in an output schema is an enum, an id, the exact words found (checked by code against the
document) or a short `reason`/`why` label; no object allows other fields; nothing could carry a summary or
replacement wording. T-21c runs on the larger model, the rest on the smaller. Each prompt is versioned and
says what the model must never do.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from app.features.after_serving import ai_tasks

from app.core.packs import default_library
from tests.support.packs import BOTH, Combo

FORBIDDEN = ("replace", "rewrite", "suggest", "instead", "better", "wording", "fix", "prose", "opinion", "summary")
WORDS = {"text_verbatim", "cited_value"}  # exact words of their document, checked by code
LABELS = {"reason", "why"}
IDS = {
    "question_id",
    "fact_id",
    "paragraph_id",
    "word_ids",
    "evidence_word_ids",
    "paragraph_ids",
    "own_paragraph_ids",
    "their_paragraph_ids",
    "para_ref",
    "paragraph_ref",
    "cited_page",
}
TASKS = {
    "T-20a": ("t20a", "sonnet"),
    "T-20b": ("t20b", "sonnet"),
    "T-21a": ("t21a", "sonnet"),
    "T-21b": ("t21b", "sonnet"),
    "T-21c": ("t21c", "opus"),
    "T-21d": ("t21d", "sonnet"),
    "T-21e": ("t21e", "sonnet"),
}


def fields(schema: dict[str, Any], name: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    yield name, schema
    for key, sub in schema.get("properties", {}).items():
        yield from fields(sub, key)
    if "items" in schema:
        yield from fields(schema["items"], name)


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_slice_g_tasks_return_only_words_ids_enums_and_short_labels(combo: Combo, task_id: str) -> None:
    builder, model = TASKS[task_id]
    task = getattr(ai_tasks, builder)(default_library().combine(*combo.codes))
    assert task.task_id == task_id and task.model == model
    assert task.prompt_version
    for name, schema in fields(dict(task.schema)):
        assert not any(word in name.lower() for word in FORBIDDEN), name
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, name
        if schema.get("type") == "string" and "enum" not in schema:
            if name in LABELS:
                assert 0 < schema["maxLength"] <= 80, name
            elif name in WORDS:
                assert 0 < schema["maxLength"] <= 2000, name
            else:
                assert name in IDS and schema["maxLength"] <= 64, name
    assert "never" in task.system.lower()


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_t21d_positions_carry_paragraph_ids_only(combo: Combo) -> None:
    task = ai_tasks.t21d(default_library().combine(*combo.codes))
    strings = {name for name, s in fields(dict(task.schema)) if s.get("type") == "string"}
    assert strings <= {"question_id", "own_paragraph_ids", "their_paragraph_ids"}


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_t20b_kinds_come_from_the_pack(combo: Combo) -> None:
    task = ai_tasks.t20b(default_library().combine(*combo.codes))
    kind = task.schema["properties"]["items"]["items"]["properties"]["kind"]
    assert kind["enum"] == list(combo.raw("jurisdiction", "pack.yaml")["written_question_classifications"])


def test_t21c_statuses_are_the_claim_results() -> None:
    task = ai_tasks.t21c(default_library().combine(*BOTH[0].codes))
    assert set(task.schema["properties"]["status"]["enum"]) == {
        "matches",
        "wrong_page",
        "wrong_value",
        "not_on_page",
        "contradicted",
    }
