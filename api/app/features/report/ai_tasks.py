"""Slice F's AI task (architecture 6.3): T-17 points at wording to check across the expert's whole report
(tone and expertise), the same schema as slice E's T-15a.

The prompt is a neutral template in `prompts/t17.md` (first line `version: ...`); the pack's prompt context
carries the specialty and the kinds of question it usually does not answer (rule 11). The schema holds a
paragraph number, a fixed kind and the flagged words only, which code checks against the paragraph (rule 1):
the AI flags, it never writes the report.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from app.core.ai.kit import AiTask
from app.core.packs import MergedPack

PROMPTS = Path(__file__).resolve().parent / "prompts"
KINDS = ("uncited_fact", "loaded_language", "outside_expertise")


@cache
def prompt(name: str) -> tuple[str, str]:
    """(version, body) of a prompt template."""
    first, _, body = (PROMPTS / f"{name}.md").read_text().partition("\n")
    key, _, version = first.partition(":")
    if key.strip() != "version" or not version.strip():
        raise ValueError(f"prompt {name} has no version line")
    return version.strip(), body.strip()


def t17(pack: MergedPack, paragraphs: int) -> AiTask:
    """Point at words in the numbered paragraphs; the words are checked against the paragraph by code."""
    version, system = prompt("t17")
    issue = {
        "type": "object",
        "additionalProperties": False,
        "required": ["sentence_index", "type", "words"],
        "properties": {
            "sentence_index": {"type": "integer", "minimum": 0, "maximum": max(paragraphs - 1, 0)},
            "type": {"type": "string", "enum": list(KINDS)},
            "words": {"type": "string", "minLength": 1, "maxLength": 200},
        },
    }
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["issues"],
        "properties": {"issues": {"type": "array", "maxItems": min(3 * max(paragraphs, 1), 100), "items": issue}},
    }
    return AiTask(task_id="T-17", prompt_version=version, model="sonnet", system=system, schema=schema)
