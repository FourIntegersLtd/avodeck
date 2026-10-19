"""Slice I's AI tasks (architecture 6.3). Prompts are neutral templates in `prompts/` (first line
`version: ...`); the pack's prompt context carries the language and vocabulary (rule 11).

- T-22a copies an agenda's numbered questions: the words exactly, with their word ids. Code checks the
  words against the agenda's own words and drops an item that does not match (rule 2).
- T-22b links each agenda item to served paragraphs by reference only; code inserts the paragraph text.
- T-22c labels the expert's draft view against the served one: a fixed status and a short reason.
- T-24 says which served document, if any, each sentence of a new note comes from: references only.

No schema has a field that could carry new wording (rule 1).
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

from app.core.ai.kit import AiTask
from app.core.packs import MergedPack

PROMPTS = Path(__file__).resolve().parent / "prompts"
STATUSES = ("consistent", "changed", "unclear")
REASON = {"type": "string", "minLength": 1, "maxLength": 40, "pattern": "^[a-z][a-z0-9_]*$"}


@cache
def prompt(name: str) -> tuple[str, str]:
    """(version, body) of a prompt template."""
    first, _, body = (PROMPTS / f"{name}.md").read_text().partition("\n")
    key, _, version = first.partition(":")
    if key.strip() != "version" or not version.strip():
        raise ValueError(f"prompt {name} has no version line")
    return version.strip(), body.strip()


def _object(required: Sequence[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(required), "properties": properties}


def t22a(pack: MergedPack) -> AiTask:
    version, system = prompt("t22a")
    item = _object(
        ["number", "text_verbatim", "word_ids", "reason"],
        {
            "number": {"type": "integer", "minimum": 1, "maximum": 500},
            "text_verbatim": {"type": "string", "minLength": 1, "maxLength": 1500},
            "word_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 400,
                "items": {"type": "string", "pattern": "^w[0-9]{1,6}$"},
            },
            "reason": REASON,
        },
    )
    schema = _object(["items"], {"items": {"type": "array", "maxItems": 200, "items": item}})
    return AiTask(task_id="T-22a", prompt_version=version, model="sonnet", system=system, schema=schema)


def t22b(pack: MergedPack, agenda_refs: Sequence[str], paragraph_refs: Sequence[str]) -> AiTask:
    version, system = prompt("t22b")
    link = _object(
        ["agenda", "paragraphs", "reason"],
        {
            "agenda": {"type": "string", "enum": list(agenda_refs)},
            "paragraphs": {
                "type": "array",
                "maxItems": 10,
                "items": {"type": "string", "enum": list(paragraph_refs)},
            },
            "reason": REASON,
        },
    )
    schema = _object(["links"], {"links": {"type": "array", "maxItems": max(len(agenda_refs), 1), "items": link}})
    return AiTask(task_id="T-22b", prompt_version=version, model="sonnet", system=system, schema=schema)


def t22c(pack: MergedPack) -> AiTask:
    version, system = prompt("t22c")
    schema = _object(["status", "reason"], {"status": {"type": "string", "enum": list(STATUSES)}, "reason": REASON})
    return AiTask(task_id="T-22c", prompt_version=version, model="opus", system=system, schema=schema)


def t24(pack: MergedPack, sentences: int, paragraph_refs: Sequence[str]) -> AiTask:
    version, system = prompt("t24")
    source: dict[str, Any] = {"anyOf": [{"type": "string", "enum": list(paragraph_refs)}, {"type": "null"}]}
    sentence = _object(
        ["sentence_index", "source", "reason"],
        {
            "sentence_index": {"type": "integer", "minimum": 0, "maximum": max(sentences - 1, 0)},
            "source": source,
            "reason": REASON,
        },
    )
    schema = _object(["sentences"], {"sentences": {"type": "array", "maxItems": max(sentences, 1), "items": sentence}})
    return AiTask(task_id="T-24", prompt_version=version, model="sonnet", system=system, schema=schema)
