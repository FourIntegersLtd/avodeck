"""Per-page extraction (T-07b, architecture 6.2 and 7 step 4): pure pieces, no database.

- `task_for(pack)`: the task with its neutral prompt and an output schema generated from the case's
  packs (source types, fact kinds and each kind's fields). No field asks for prose (rule 1).
- `page_text(words, stripped)`: the page as one word per line, `<word id> <word>`, built only from
  the identifier-stripped text, so a masked word keeps its ID but never its text (rule 7).
- `sort_facts(output, words, struck)`: facts citing a crossed-out word are dropped; the rest are
  checked against the words they cite (rule 2) and split into kept and refused.
- `box(word_ids, polygons, width, height)`: the cited words' box as a share of the page.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from app.core.ai import provenance
from app.core.ai.kit import AiTask
from app.core.packs import MergedPack
from app.features.pipeline import dates

TASK_ID = "T-07b"
PROMPT_VERSION = "2026-10-17.1"
MODEL_ROLE = "sonnet"
AVAILABILITY = ("received", "filed", "seen", "sent_to_clinician")
_FIELD_TYPES = {"date": ["string", "null"], "string": ["string", "null"], "boolean": ["boolean", "null"]}
_SHORT = 120
_SPAN = 4000


@cache
def _system() -> str:
    return (Path(__file__).parent / "prompts" / "t07b.txt").read_text()


def _ids() -> dict[str, Any]:
    return {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1, "maxLength": 40}}


def _dated(**properties: Any) -> dict[str, Any]:
    props = {**properties, "date": {"type": ["string", "null"], "maxLength": 40}, "word_ids": _ids()}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def output_schema(pack: MergedPack) -> dict[str, Any]:
    kinds = pack.fact_kinds()
    fields_of = [
        {
            "if": {"properties": {"kind": {"const": kind["key"]}}},
            "then": {
                "properties": {
                    "fields": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            name: {"type": _FIELD_TYPES.get(str(t), ["string", "null"]), "maxLength": _SPAN}
                            for name, t in kind.get("fields", {}).items()
                        },
                    }
                }
            },
        }
        for kind in kinds
    ]
    fact = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "clinician_role", "fields", "value", "word_ids"],
        "properties": {
            "kind": {"enum": [k["key"] for k in kinds]},
            "clinician_role": {"type": ["string", "null"], "maxLength": _SHORT},
            "fields": {"type": "object"},
            "value": {"type": "string", "minLength": 1, "maxLength": _SPAN},
            "word_ids": _ids(),
        },
        "allOf": fields_of,
    }
    action = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "word_ids"],
        "properties": {"text": {"type": "string", "maxLength": _SPAN}, "word_ids": _ids()},
    }
    properties = {
        "source_type": {"enum": [*(s["key"] for s in pack.source_types()), None]},  # null: cannot tell
        "printed_dates": {"type": "array", "items": {"type": "string", "maxLength": 40}},
        "facts": {"type": "array", "items": fact},
        "references_out": {
            "type": "array",
            "items": _dated(described_document={"type": "string", "maxLength": _SPAN}),
        },
        "availability_evidence": {"type": "array", "items": _dated(kind={"enum": list(AVAILABILITY)})},
        "actions_requested_of_clinician": {"type": "array", "items": action},
    }
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def task_for(pack: MergedPack) -> AiTask:
    return AiTask(
        task_id=TASK_ID, prompt_version=PROMPT_VERSION, model=MODEL_ROLE, system=_system(), schema=output_schema(pack)
    )


def plain_text(words: Sequence[tuple[str, str]]) -> str:
    """The page's words joined by single spaces, for identifier stripping."""
    return " ".join(" ".join(text.split()) for _, text in words)


def page_text(words: Sequence[tuple[str, str]], stripped: str) -> str:
    """One line per word, `<id> <word>`, every word taken from `stripped` (`plain_text` after
    `pii.strip`). A run of words replaced by a placeholder is given the first word's ID."""
    ids = [word_id for word_id, text in words for _ in text.split()]
    before = plain_text(words).split(" ")
    after = stripped.split(" ")
    lines: list[str] = []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            lines.extend(f"{ids[i]} {after[j]}" for i, j in zip(range(i1, i2), range(j1, j2), strict=True))
        elif j2 > j1:
            at = ids[i1] if i1 < len(ids) else (ids[-1] if ids else "")
            if at:
                lines.append(f"{at} {' '.join(after[j1:j2])}")
    seen: dict[str, list[str]] = {}
    for line in lines:
        word_id, _, text = line.partition(" ")
        seen.setdefault(word_id, []).append(text)
    return "\n".join(f"{word_id} {' '.join(texts)}" for word_id, texts in seen.items())


@dataclass(frozen=True)
class Sorted:
    kept: list[Mapping[str, Any]]
    refused: int
    struck: int


def sort_facts(output: Mapping[str, Any], words: Mapping[str, str], struck: set[str]) -> Sorted:
    facts = list(output.get("facts", []))
    clean = [f for f in facts if not set(f.get("word_ids", [])) & struck]
    kept, refused = provenance.check_items(clean, words)
    return Sorted(kept=list(kept), refused=len(refused), struck=len(facts) - len(clean))


def sort_evidence(
    output: Mapping[str, Any] | None, ordered: Sequence[tuple[str, str]], struck: set[str], pack: MergedPack
) -> list[dict[str, Any]]:
    """The availability evidence worth keeping: `{kind, date, date_text, word_ids}`.

    Checked like a fact: every cited word is on the page and not crossed out, and a date the model
    points at is printed in the cited words. The date itself is read by code in the pack's date
    order; one that reads more than one way is kept with no date, never guessed. Anything else is
    dropped.
    """
    on_page = dict(ordered)
    kept: list[dict[str, Any]] = []
    for item in (output or {}).get("availability_evidence", []):
        ids = list(item.get("word_ids", []))
        if item.get("kind") not in AVAILABILITY or not ids or any(i not in on_page or i in struck for i in ids):
            continue
        cited = [(w, t) for w, t in ordered if w in ids]
        found = dates.find(cited, pack)
        pointed = item.get("date")
        value: Any = None
        if pointed is not None and str(pointed).strip():
            value, _ = dates.event_date(str(pointed), found, pack)
            if value is None and not any(d.ambiguous for d in found):
                continue  # the cited words do not print that date
        else:
            readable = {d.value for d in found if not d.ambiguous}
            value = readable.pop() if len(readable) == 1 and len(found) == 1 else None
        text = None
        if value is not None:
            hit = next(d for d in found if value in (d.value, d.other))
            text = " ".join(on_page[w] for w in hit.word_ids)
        kept.append(
            {"kind": item["kind"], "date": value.isoformat() if value else None, "date_text": text, "word_ids": ids}
        )
    return kept


def box(
    word_ids: Sequence[str], polygons: Mapping[str, Sequence[float]], width: float, height: float
) -> dict[str, float]:
    xs = [x for w in word_ids for x in polygons[w][0::2]]
    ys = [y for w in word_ids for y in polygons[w][1::2]]

    def share(value: float, whole: float) -> float:
        return round(min(1.0, max(0.0, value / whole)), 5)

    return {
        "x0": share(min(xs), width),
        "y0": share(min(ys), height),
        "x1": share(max(xs), width),
        "y1": share(max(ys), height),
    }
