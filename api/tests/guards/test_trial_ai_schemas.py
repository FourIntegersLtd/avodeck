"""Rule 1 for slice I's AI tasks (architecture 6.3).

- T-22a copies agenda questions: verbatim text with word ids that code checks against the agenda's words.
- T-22b links agenda items to served paragraphs by reference only; code inserts the paragraph text.
- T-22c labels a draft position against the served report: a fixed status and a short reason.
- T-24 says which served document, if any, each note sentence comes from: references only.
No schema has a field that could carry new wording, and no object allows other fields.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.core.packs import default_library
from app.features.trial import ai_tasks
from tests.support.packs import BOTH, Combo

FORBIDDEN = ("replace", "rewrite", "suggest", "instead", "better", "wording", "prose", "opinion", "position")


def fields(schema: dict[str, Any], name: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    yield name, schema
    for key, sub in schema.get("properties", {}).items():
        yield from fields(sub, key)
    if "items" in schema:
        yield from fields(schema["items"], name)
    for sub in schema.get("anyOf", []):
        yield from fields(sub, name)


def strings(task: Any) -> dict[str, dict[str, Any]]:
    out = {}
    for name, schema in fields(dict(task.schema)):
        assert not any(word in name.lower() for word in FORBIDDEN), name
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, name
        if schema.get("type") == "string" and "enum" not in schema:
            out[name] = schema
    return out


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_every_task_is_neutral_and_says_never(combo: Combo) -> None:
    pack = default_library().combine(*combo.codes)
    for task in (
        ai_tasks.t22a(pack),
        ai_tasks.t22b(pack, ["a1"], ["p1", "p2"]),
        ai_tasks.t22c(pack),
        ai_tasks.t24(pack, 3, ["p1"]),
    ):
        prompt = task.system.lower()
        assert "never" in prompt, task.task_id
        assert task.prompt_version, task.task_id


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_t22a_copies_verbatim_text_checked_against_word_ids(combo: Combo) -> None:
    task = ai_tasks.t22a(default_library().combine(*combo.codes))
    assert task.task_id == "T-22a" and task.model == "sonnet"
    free = strings(task)
    assert set(free) <= {"text_verbatim", "reason", "word_ids"}
    assert free["reason"]["maxLength"] <= 80


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_t22b_returns_ids_only(combo: Combo) -> None:
    task = ai_tasks.t22b(default_library().combine(*combo.codes), ["a1", "a2"], ["p1", "p2", "p3"])
    assert task.task_id == "T-22b" and task.model == "sonnet"
    free = strings(task)
    assert set(free) <= {"reason"}
    refs = [s for n, s in fields(dict(task.schema)) if "enum" in s]
    assert {tuple(s["enum"]) for s in refs} >= {("a1", "a2"), ("p1", "p2", "p3")}


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_t22c_returns_a_fixed_status_and_a_short_reason(combo: Combo) -> None:
    task = ai_tasks.t22c(default_library().combine(*combo.codes))
    assert task.task_id == "T-22c" and task.model == "opus"
    assert set(strings(task)) == {"reason"}
    [status] = [s for n, s in fields(dict(task.schema)) if n == "status"]
    assert status["enum"] == ["consistent", "changed", "unclear"]


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_t24_points_at_sources_only(combo: Combo) -> None:
    task = ai_tasks.t24(default_library().combine(*combo.codes), 4, ["p1", "p2"])
    assert task.task_id == "T-24" and task.model == "sonnet"
    assert set(strings(task)) <= {"reason"}
