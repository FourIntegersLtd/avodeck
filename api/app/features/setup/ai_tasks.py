"""Slice C's AI tasks (architecture 6.3): T-06 questions and assumptions, T-04 prefill suggestions, T-02
template heading sections.

Prompts are neutral templates in `prompts/*.md` (first line `version: ...`); the pack's prompt context
and the output schemas (built from the pack's registries) carry everything country- or specialty-
specific (rule 11). The schemas hold copied words, word ids, pack keys and a short `reason` label
only (rule 1).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from app.core.ai.kit import AiTask
from app.core.packs import MergedPack

PROMPTS = Path(__file__).resolve().parent / "prompts"
REASON = {"type": "string", "maxLength": 80}
WORD_IDS = {"type": "array", "minItems": 1, "maxItems": 400, "items": {"type": "string", "maxLength": 40}}
# Labels the server replaces (title over 40 characters: the question's first clause; paragraph reference: read
# from the letter's layout), so a little too long must not make the whole output invalid.
PARA_REF = {"type": ["string", "null"], "maxLength": 80}
OTHER_SECTION = "other"
PREFILL_FIELDS = ("instructing_party_name", "instructing_party_type", "party_role", "due_date", "court_track")


@cache
def prompt(name: str) -> tuple[str, str]:
    """(version, body) of a prompt template."""
    first, _, body = (PROMPTS / f"{name}.md").read_text().partition("\n")
    key, _, version = first.partition(":")
    if key.strip() != "version" or not version.strip():
        raise ValueError(f"prompt {name} has no version line")
    return version.strip(), body.strip()


def t06(pack: MergedPack) -> AiTask:
    version, system = prompt("t06")
    kinds = [q["key"] for q in pack.question_types()]
    copied = {
        "text_verbatim": {"type": "string", "minLength": 1, "maxLength": 3000},
        "word_ids": WORD_IDS,
        "para_ref": PARA_REF,
        "reason": REASON,
    }
    question = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text_verbatim", "word_ids", "para_ref", "kind", "reason"],
        "properties": {
            **copied,
            "kind": {"type": "string", "enum": kinds},
            "short_title": {"type": "string", "minLength": 1, "maxLength": 80},
        },
    }
    assumption = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text_verbatim", "word_ids", "para_ref", "reason"],
        "properties": copied,
    }
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["questions", "assumptions"],
        "properties": {
            "questions": {"type": "array", "maxItems": 60, "items": question},
            "assumptions": {"type": "array", "maxItems": 60, "items": assumption},
        },
    }
    return AiTask(task_id="T-06", prompt_version=version, model="sonnet", system=system, schema=schema)


def t04(pack: MergedPack) -> AiTask:
    version, system = prompt("t04")
    suggestion = {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "value", "word_ids", "reason"],
        "properties": {
            "field": {"type": "string", "enum": list(PREFILL_FIELDS)},
            "value": {"type": "string", "minLength": 1, "maxLength": 300},
            "word_ids": WORD_IDS,
            "reason": REASON,
        },
    }
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["suggestions"],
        "properties": {"suggestions": {"type": "array", "maxItems": 20, "items": suggestion}},
    }
    return AiTask(task_id="T-04", prompt_version=version, model="sonnet", system=system, schema=schema)


def t02(pack: MergedPack) -> AiTask:
    """Suggest a report section (the pack's keys plus `other`) for each template heading."""
    version, system = prompt("t02")
    sections = [*(s["key"] for s in pack.report_sections()), OTHER_SECTION]
    suggestion = {
        "type": "object",
        "additionalProperties": False,
        "required": ["index", "section", "reason"],
        "properties": {
            "index": {"type": "integer", "minimum": 0},
            "section": {"type": "string", "enum": sections},
            "reason": REASON,
        },
    }
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["suggestions"],
        "properties": {"suggestions": {"type": "array", "maxItems": 200, "items": suggestion}},
    }
    return AiTask(task_id="T-02", prompt_version=version, model="sonnet", system=system, schema=schema)


def t02_text(headings: list[dict[str, Any]]) -> str:
    """What T-02 reads: one heading per line, its paragraph index, a tab, then its words."""
    return "\n".join(f"{h['index']}\t{' '.join(str(h['text']).split())}" for h in headings)
