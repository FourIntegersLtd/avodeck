"""Slice D's AI tasks (architecture 6.3): T-11 suggests fact–question links, T-13a labels the spans a
check points at.

Prompts are neutral templates in `prompts/*.md` (first line `version: ...`); the pack's prompt context
carries the fact kinds (rule 11). Schemas hold ids, a fixed label list, word ids and a short `reason`
only (rule 1). Code keeps only what it offered: unknown ids and words outside a span are dropped.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any

from app.core.ai.kit import AiTask
from app.core.packs import MergedPack

PROMPTS = Path(__file__).resolve().parent / "prompts"
ID = {"type": "string", "maxLength": 40}
REASON = {"type": "string", "maxLength": 80}
WORD_IDS = {"type": "array", "minItems": 1, "maxItems": 400, "items": {"type": "string", "maxLength": 40}}
LABELS = ("action_taken", "no_action", "safety_netting", "none")


@cache
def prompt(name: str) -> tuple[str, str]:
    """(version, body) of a prompt template."""
    first, _, body = (PROMPTS / f"{name}.md").read_text().partition("\n")
    key, _, version = first.partition(":")
    if key.strip() != "version" or not version.strip():
        raise ValueError(f"prompt {name} has no version line")
    return version.strip(), body.strip()


def _list(name: str, item: dict[str, Any], max_items: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [name],
        "properties": {name: {"type": "array", "maxItems": max_items, "items": item}},
    }


def t11(pack: MergedPack) -> AiTask:
    version, system = prompt("t11")
    link = {
        "type": "object",
        "additionalProperties": False,
        "required": ["fact_id", "question_id", "relevant", "reason"],
        "properties": {"fact_id": ID, "question_id": ID, "relevant": {"type": "boolean"}, "reason": REASON},
    }
    return AiTask(
        task_id="T-11", prompt_version=version, model="sonnet", system=system, schema=_list("links", link, 400)
    )


def t13a(pack: MergedPack) -> AiTask:
    version, system = prompt("t13a")
    label = {
        "type": "object",
        "additionalProperties": False,
        "required": ["fact_id", "label", "word_ids"],
        "properties": {"fact_id": ID, "label": {"type": "string", "enum": list(LABELS)}, "word_ids": WORD_IDS},
    }
    return AiTask(
        task_id="T-13a", prompt_version=version, model="sonnet", system=system, schema=_list("labels", label, 200)
    )


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def t11_text(
    questions: Sequence[tuple[uuid.UUID, str]],
    candidates: Sequence[tuple[uuid.UUID, str, str | None, str | None]],
) -> str:
    lines = [f"question\t{qid}\t{_one_line(words)}" for qid, words in questions]
    lines += [f"fact\t{fid}\t{kind}\t{day or ''}\t{_one_line(value)}" for fid, kind, day, value in candidates]
    return "\n".join(lines)


def t13a_text(rule: str, candidates: Sequence[tuple[uuid.UUID, Sequence[tuple[str, str]]]]) -> str:
    lines = [f"rule\t{rule}"]
    for fid, words in candidates:
        lines.append(f"fact\t{fid}")
        lines += [f"{wid} {_one_line(text)}" for wid, text in words]
    return "\n".join(lines)


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def accept_links(
    output: Mapping[str, Any], offered: Mapping[uuid.UUID, Collection[uuid.UUID]]
) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """(fact_id, question_id, reason) for each relevant pair the code offered, once."""
    kept: dict[tuple[uuid.UUID, uuid.UUID], str] = {}
    for link in output.get("links", []):
        fid, qid = _uuid(link.get("fact_id")), _uuid(link.get("question_id"))
        if fid is None or qid is None or not link.get("relevant") or fid not in offered.get(qid, ()):
            continue
        kept.setdefault((fid, qid), str(link.get("reason", "")))
    return [(fid, qid, reason) for (fid, qid), reason in kept.items()]


def accept_labels(output: Mapping[str, Any], spans: Mapping[uuid.UUID, Collection[str]]) -> dict[uuid.UUID, str]:
    """fact_id → label, for the first label per candidate whose words lie inside its span."""
    kept: dict[uuid.UUID, str] = {}
    for label in output.get("labels", []):
        fid = _uuid(label.get("fact_id"))
        words = set(label.get("word_ids", []))
        if fid is None or fid in kept or fid not in spans or not words or not words <= set(spans[fid]):
            continue
        if label.get("label") in LABELS:
            kept[fid] = str(label["label"])
    return kept
