"""Per-page extraction (T-07b, architecture 6.2 and 7 step 4): pure pieces, no database.

- `task_for(pack)`: the task with its neutral prompt and a batched output schema generated from the
  case's packs (source types, fact kinds and each kind's fields). No field asks for prose (rule 1).
- `page_schema(pack)`: one page's shape (`source_type`, `facts`, `entries`, ...).
- `output_schema(pack)`: T-07b's real answer shape, `{"pages": [page_schema plus "page_no", ...]}`.
  Several pages are asked about in one call (to stay inside the account-wide Bedrock on-demand
  throughput ceiling), so every page's answer is keyed by its own `page_no`: word IDs are only
  unique within a page (`ocr_words` is `unique(page_id, n)`), so a flat cross-page list of facts
  would risk resolving one page's word ID against another page's words (rule 2). Each page's facts
  and entries are checked only against that page's own word map (`sort_facts`/`sort_entries`).
- `batch_text(pages)`: the prompt text for a batch: every page's own `page_text`, headed by its
  page number, so the model can tell them apart and answer each one under the right `page_no`.
- `pages_of(output)`: the batch answer's pages keyed by `page_no`.
- `page_text(words, stripped)`: the page as one word per line, `<word id> <word>`, built only from
  the identifier-stripped text, so a masked word keeps its ID but never its text (rule 7).
- `sort_facts(output, words, struck)`: facts citing a crossed-out word are dropped; the rest are
  checked against the words they cite (rule 2) and split into kept and refused.
- `sort_entries(output, positions, pack)`: the page's entries (ADR 0028) whose first and last words are on
  the page, in order, outside each other and in the pack's vocabulary; the rest are counted, never logged.
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
from app.core.contracts.document_words import box as box  # used here as `extract.box`
from app.core.packs import MergedPack
from app.features.pipeline import dates

TASK_ID = "T-07b"
PROMPT_VERSION = "2026-11-01.1"  # batched, multi-page prompt (page-keyed output, see output_schema)
MODEL_ROLE = "sonnet"
AVAILABILITY = ("received", "filed", "seen", "sent_to_clinician")
_FIELD_TYPES = {"date": ["string", "null"], "string": ["string", "null"], "boolean": ["boolean", "null"]}
_SHORT = 120
_SPAN = 4000
BATCH_MAX_TOKENS = 16000  # enough structured output for a batch of ~100 pages, well under the model's limit
PAGE_MARK = "=== PAGE {0} ==="


@cache
def _system() -> str:
    return (Path(__file__).parent / "prompts" / "t07b.txt").read_text()


def _ids() -> dict[str, Any]:
    return {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1, "maxLength": 40}}


def _dated(**properties: Any) -> dict[str, Any]:
    props = {**properties, "date": {"type": ["string", "null"], "maxLength": 40}, "word_ids": _ids()}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def page_schema(pack: MergedPack) -> dict[str, Any]:
    """One page's answer shape (source type, printed dates, facts, entries, ...)."""
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
    entry = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "first_word_id", "last_word_id", "author_role", "specialty"],
        "properties": {
            "kind": {"enum": [k["key"] for k in pack.entry_kinds()]},
            "first_word_id": {"type": "string", "minLength": 1, "maxLength": 40},
            "last_word_id": {"type": "string", "minLength": 1, "maxLength": 40},
            "author_role": {"enum": [*(r["key"] for r in pack.author_roles()), None]},
            "specialty": {"enum": [*(s["key"] for s in pack.entry_specialties()), None]},
        },
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
        "entries": {"type": "array", "items": entry, **({} if pack.entry_kinds() else {"maxItems": 0})},
    }
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def output_schema(pack: MergedPack) -> dict[str, Any]:
    """T-07b's real answer: `page_schema` repeated once per page asked about in the batch, each
    carrying the `page_no` it was given, so pages are never resolved against each other's words."""
    page = page_schema(pack)
    page = {
        **page,
        "required": ["page_no", *page["required"]],
        "properties": {"page_no": {"type": "integer", "minimum": 1}, **page["properties"]},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["pages"],
        "properties": {"pages": {"type": "array", "minItems": 1, "items": page}},
    }


def task_for(pack: MergedPack) -> AiTask:
    return AiTask(
        task_id=TASK_ID,
        prompt_version=PROMPT_VERSION,
        model=MODEL_ROLE,
        system=_system(),
        schema=output_schema(pack),
        max_tokens=BATCH_MAX_TOKENS,
    )


def pages_of(output: Mapping[str, Any] | None) -> dict[int, Mapping[str, Any]]:
    """The batch answer's pages keyed by `page_no`; a repeated `page_no` keeps the first one."""
    found: dict[int, Mapping[str, Any]] = {}
    for item in (output or {}).get("pages", []):
        no = item.get("page_no")
        if isinstance(no, int) and no not in found:
            found[no] = item
    return found


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


def batch_text(pages: Sequence[tuple[int, str]]) -> str:
    """The prompt text for a batch of pages: each page's own `page_text` (already identifier-stripped
    and, where there is one, headed with the previous page's top), introduced by its page number so
    the model answers each one under its own `page_no` in `output_schema`'s `pages[]`."""
    return "\n\n".join(f"{PAGE_MARK.format(page_no)}\n{text}" for page_no, text in pages)


HEADER_WORDS = 15
HEADER_MARK = "Previous page header (context only, words without IDs, never cite them):"


def with_header(page: str, header: str) -> str:
    """The page's lines, led by the already identifier-stripped top of the page before it."""
    return f"{HEADER_MARK}\n{header}\n\n{page}" if header.strip() else page


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


@dataclass(frozen=True)
class Entry:
    kind: str
    first_word_id: str
    last_word_id: str
    first_n: int
    last_n: int
    author_role: str | None
    specialty: str | None


@dataclass(frozen=True)
class SortedEntries:
    kept: list[Entry]
    rejected: int


def sort_entries(output: Mapping[str, Any] | None, positions: Mapping[str, int], pack: MergedPack) -> SortedEntries:
    """The entries worth keeping, in page order, and how many were dropped.

    An entry is kept when its kind, author role and specialty are in the pack, both ends are words on the
    page (`positions`: word id to its number, from 1), the first is not after the last, and it does not
    overlap an entry already kept. Nothing about a dropped entry is logged but the count."""
    kinds = {k["key"] for k in pack.entry_kinds()}
    roles = {r["key"] for r in pack.author_roles()}
    specialties = {s["key"] for s in pack.entry_specialties()}
    kept: list[Entry] = []
    rejected = 0
    for item in (output or {}).get("entries", []):
        first, last = positions.get(item.get("first_word_id")), positions.get(item.get("last_word_id"))
        role, specialty = item.get("author_role"), item.get("specialty")
        ok = (
            item.get("kind") in kinds
            and first is not None
            and last is not None
            and first <= last
            and (role is None or role in roles)
            and (specialty is None or specialty in specialties)
            and not any(first <= e.last_n and e.first_n <= last for e in kept)
        )
        if not ok:
            rejected += 1
            continue
        assert first is not None and last is not None
        kept.append(
            Entry(
                kind=str(item["kind"]),
                first_word_id=str(item["first_word_id"]),
                last_word_id=str(item["last_word_id"]),
                first_n=first,
                last_n=last,
                author_role=role,
                specialty=specialty,
            )
        )
    return SortedEntries(kept=sorted(kept, key=lambda e: e.first_n), rejected=rejected)


def entry_date(
    entry: Entry, ordered: Sequence[tuple[str, str]], struck: set[str], pack: MergedPack
) -> tuple[Any, str | None]:
    """An entry's date from the dates printed inside its words: one readable date is used, a date that reads
    more than one way is left for the reader to check, and none gives no date."""
    inside = [
        (w, t) for n, (w, t) in enumerate(ordered, start=1) if entry.first_n <= n <= entry.last_n and w not in struck
    ]
    found = dates.find(inside, pack)
    readable = {d.value for d in found if not d.ambiguous}
    if len(readable) == 1 and len(found) == 1:
        return readable.pop(), dates.PRINTED
    if found and not readable:
        return None, dates.PLEASE_CHECK
    return None, None
