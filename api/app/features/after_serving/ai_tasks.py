"""Slice G's AI tasks (architecture 6.3): T-20a/b written questions, T-21a-e new records, the other
expert's report and dependent paragraphs.

Prompts are neutral templates in `prompts/*.md` (first line `version: ...`); the pack's prompt context and
the schemas built from its registries carry anything country- or specialty-specific (rule 11). Every
string a schema allows is an id, an enum, the exact words of a document (checked by code afterwards) or a
short `reason`/`why` label (rule 1). T-21c runs on the larger model; the rest on the smaller.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from app.core.ai.kit import AiTask
from app.core.packs import MergedPack

PROMPTS = Path(__file__).resolve().parent / "prompts"
REASON = {"type": "string", "minLength": 1, "maxLength": 80}
ID = {"type": "string", "minLength": 1, "maxLength": 64}
IDS = {"type": "array", "maxItems": 200, "items": ID}
WORD_IDS = {"type": "array", "maxItems": 400, "items": {"type": "string", "maxLength": 40}}
WORDS = {"type": "string", "minLength": 1, "maxLength": 2000}
REF = {"type": ["string", "null"], "maxLength": 20}
CLAIM_RESULTS = ["matches", "wrong_page", "wrong_value", "not_on_page", "contradicted"]


@cache
def prompt(name: str) -> tuple[str, str]:
    """(version, body) of a prompt template."""
    first, _, body = (PROMPTS / f"{name}.md").read_text().partition("\n")
    key, _, version = first.partition(":")
    if key.strip() != "version" or not version.strip():
        raise ValueError(f"prompt {name} has no version line")
    return version.strip(), body.strip()


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def _list(name: str, item: dict[str, Any], most: int = 400) -> dict[str, Any]:
    return _object({name: {"type": "array", "maxItems": most, "items": item}})


def _task(task_id: str, name: str, model: str, schema: dict[str, Any]) -> AiTask:
    version, system = prompt(name)
    return AiTask(task_id=task_id, prompt_version=version, model=model, system=system, schema=schema)


def t20a(pack: MergedPack) -> AiTask:
    """Find each written question as the exact words of the questions document."""
    item = _object({"text_verbatim": WORDS, "word_ids": WORD_IDS, "para_ref": REF, "reason": REASON})
    return _task("T-20a", "t20a", "sonnet", _list("questions", item))


def t20b(pack: MergedPack) -> AiTask:
    """Suggest each question's classification (from the pack) and the served paragraphs it is about."""
    kinds = list(pack.written_question_classifications())
    item = _object(
        {"question_id": ID, "kind": {"type": "string", "enum": kinds}, "paragraph_ids": IDS, "reason": REASON}
    )
    return _task("T-20b", "t20b", "sonnet", _list("items", item))


def t21a(pack: MergedPack) -> AiTask:
    """Which facts on pages added since serving may matter to which question (ids and a short label)."""
    item = _object({"fact_id": ID, "question_id": ID, "why": REASON})
    return _task("T-21a", "t21a", "sonnet", _list("items", item))


def t21b(pack: MergedPack) -> AiTask:
    """Find each claim in the other expert's report that cites a page, as their exact words."""
    item = _object(
        {
            "text_verbatim": WORDS,
            "word_ids": WORD_IDS,
            "paragraph_ref": REF,
            "cited_page": {"type": "string", "minLength": 1, "maxLength": 20},
            "cited_value": WORDS,
        }
    )
    return _task("T-21b", "t21b", "sonnet", _list("claims", item))


def t21c(pack: MergedPack) -> AiTask:
    """Compare one claim with the words of the page it cites (only claims code could not settle)."""
    schema = _object(
        {"status": {"type": "string", "enum": CLAIM_RESULTS}, "evidence_word_ids": WORD_IDS, "reason": REASON}
    )
    return _task("T-21c", "t21c", "opus", schema)


def t21d(pack: MergedPack) -> AiTask:
    """Link the other expert's paragraphs to the expert's own, per question, by id only."""
    item = _object({"question_id": ID, "own_paragraph_ids": IDS, "their_paragraph_ids": IDS})
    return _task("T-21d", "t21d", "sonnet", _list("positions", item, 100))


def t21e(pack: MergedPack) -> AiTask:
    """Which other served paragraphs may depend on a changed one (the expert reviews each)."""
    item = _object({"paragraph_id": ID, "depends": {"type": "boolean"}, "reason": REASON})
    return _task("T-21e", "t21e", "sonnet", _list("items", item, 200))
