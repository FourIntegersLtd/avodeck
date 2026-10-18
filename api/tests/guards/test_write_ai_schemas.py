"""Rule 1 for slice E's AI tasks: the output schemas hold references, pack codes and short labels only.

Every string the model may return is an enum, a short label (80 characters at most), or a quote the
server then checks against the source words (a witness statement's claim, the words a warning flags).
No schema has a field for replacement or suggested wording, and none allows other fields.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.core.ai.kit import AiTask
from app.core.packs import default_library
from app.features.write import ai_tasks
from tests.support.packs import BOTH, Combo

QUOTES = {"text_verbatim": 400, "words": 200}  # checked by code against the statement or the sentence
FORBIDDEN = ("replace", "rewrite", "suggest", "instead", "better", "wording", "fix", "prose", "opinion")


def tasks(combo: Combo) -> list[AiTask]:
    pack = default_library().combine(*combo.codes)
    return [
        ai_tasks.t14a(pack, ["f1"]),
        ai_tasks.t14b(pack, ["f1"]),
        ai_tasks.t14c(pack, ["f1"]),
        ai_tasks.t15a(pack, 3),
    ]


def fields(schema: dict[str, Any], name: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    yield name, schema
    for key, sub in schema.get("properties", {}).items():
        yield from fields(sub, key)
    if "items" in schema:
        yield from fields(schema["items"], name)


@pytest.mark.parametrize("combo", BOTH, ids=["launch", "fixture"])
def test_every_string_is_an_enum_a_short_label_or_a_checked_quote(combo: Combo) -> None:
    for task in tasks(combo):
        for name, schema in fields(dict(task.schema)):
            assert not any(word in name.lower() for word in FORBIDDEN), (task.task_id, name)
            if schema.get("type") == "object":
                assert schema.get("additionalProperties") is False, (task.task_id, name)
            if schema.get("type") == "string" and "enum" not in schema:
                limit = QUOTES.get(name, 80)
                assert 0 < schema.get("maxLength", 10**9) <= limit, (task.task_id, name)


def test_the_tasks_are_the_slices_four() -> None:
    assert [t.task_id for t in tasks(BOTH[0])] == ["T-14a", "T-14b", "T-14c", "T-15a"]
