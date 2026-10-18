"""Slice E's AI tasks (architecture 6.3): T-14a sorts facts by side, T-14b picks
citable facts the draft does not use yet, T-14c sets a witness statement's claims beside the facts.

Prompts are neutral templates in `prompts/*.md` (first line `version: ...`); the pack's prompt context
and the output schemas (built from the pack's registries and the references a request supplies) carry
everything case-type specific (rule 11). The schemas hold references, pack codes and a short `reason`
label only (rule 1): the AI sorts and flags, it never writes the opinion.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

from app.core.ai.kit import AiTask
from app.core.packs import MergedPack

PROMPTS = Path(__file__).resolve().parent / "prompts"
REASON = {"type": "string", "maxLength": 80}


@cache
def prompt(name: str) -> tuple[str, str]:
    """(version, body) of a prompt template."""
    first, _, body = (PROMPTS / f"{name}.md").read_text().partition("\n")
    key, _, version = first.partition(":")
    if key.strip() != "version" or not version.strip():
        raise ValueError(f"prompt {name} has no version line")
    return version.strip(), body.strip()


def t14a(pack: MergedPack, refs: Sequence[str]) -> AiTask:
    """Sort the supplied facts (by reference) onto the pack's sides; a reference not supplied fails the schema."""
    version, system = prompt("t14a")
    point = {
        "type": "object",
        "additionalProperties": False,
        "required": ["fact", "side", "reason"],
        "properties": {
            "fact": {"type": "string", "enum": list(refs)},
            "side": {"type": "string", "enum": [s["code"] for s in pack.point_sides()]},
            "reason": REASON,
        },
    }
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["points"],
        "properties": {"points": {"type": "array", "maxItems": len(refs), "items": point}},
    }
    return AiTask(task_id="T-14a", prompt_version=version, model="sonnet", system=system, schema=schema)


def t14b(pack: MergedPack, refs: Sequence[str]) -> AiTask:
    """Pick the supplied facts (by reference) that bear on the draft; a reference not supplied fails the schema."""
    version, system = prompt("t14b")
    fact = {
        "type": "object",
        "additionalProperties": False,
        "required": ["fact", "why_relevant"],
        "properties": {"fact": {"type": "string", "enum": list(refs)}, "why_relevant": REASON},
    }
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["facts"],
        "properties": {"facts": {"type": "array", "maxItems": len(refs), "items": fact}},
    }
    return AiTask(task_id="T-14b", prompt_version=version, model="sonnet", system=system, schema=schema)


VERDICTS = ("supports", "contradicts", "not_addressed")
MAX_CLAIMS = 40


def t14c(pack: MergedPack, refs: Sequence[str]) -> AiTask:
    """Quote a statement's claims and label each against the supplied facts (by reference). The quote is
    checked against the statement's words by code; a reference not supplied fails the schema."""
    version, system = prompt("t14c")
    claim = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text_verbatim"],
        "properties": {"text_verbatim": {"type": "string", "minLength": 1, "maxLength": 400}},
    }
    verdict = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim_index", "fact_ids", "verdict"],
        "properties": {
            "claim_index": {"type": "integer", "minimum": 0, "maximum": MAX_CLAIMS - 1},
            "fact_ids": {"type": "array", "maxItems": len(refs), "items": {"type": "string", "enum": list(refs)}},
            "verdict": {"type": "string", "enum": list(VERDICTS)},
        },
    }
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claims", "verdicts"],
        "properties": {
            "claims": {"type": "array", "maxItems": MAX_CLAIMS, "items": claim},
            "verdicts": {"type": "array", "maxItems": MAX_CLAIMS, "items": verdict},
        },
    }
    return AiTask(task_id="T-14c", prompt_version=version, model="opus", system=system, schema=schema)
