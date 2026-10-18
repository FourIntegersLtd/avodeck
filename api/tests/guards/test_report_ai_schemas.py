"""Rule 1 for slice F's AI task: T-17 points at words in the expert's own paragraphs (tone and expertise)
and nothing more. Every string is an enum or the flagged words, which code checks against the paragraph;
no field could carry replacement wording and no object allows other fields."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.core.packs import default_library
from app.features.report import ai_tasks
from tests.support.packs import BOTH, Combo

FORBIDDEN = ("replace", "rewrite", "suggest", "instead", "better", "wording", "fix", "prose", "opinion")


def fields(schema: dict[str, Any], name: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    yield name, schema
    for key, sub in schema.get("properties", {}).items():
        yield from fields(sub, key)
    if "items" in schema:
        yield from fields(schema["items"], name)


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_t17_returns_only_places_kinds_and_checked_words(combo: Combo) -> None:
    task = ai_tasks.t17(default_library().combine(*combo.codes), 5)
    assert task.task_id == "T-17" and task.model == "sonnet"
    for name, schema in fields(dict(task.schema)):
        assert not any(word in name.lower() for word in FORBIDDEN), name
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, name
        if schema.get("type") == "string" and "enum" not in schema:
            assert name == "words" and 0 < schema["maxLength"] <= 200
    prompt = task.system.lower()
    assert "never" in prompt and "conclude" in prompt
