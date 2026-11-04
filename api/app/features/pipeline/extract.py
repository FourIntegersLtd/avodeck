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

import datetime as dt
import difflib
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

from app.core.ai import provenance
from app.core.ai.kit import AiTask
from app.core.contracts.document_words import box as box  # used here as `extract.box`
from app.core.packs import MergedPack
from app.features.pipeline import date_repair, dates

TASK_ID = "T-07b"
logger = logging.getLogger(__name__)

PROMPT_VERSION = (
    "2026-11-04.1"  # entries cover every word; batched, multi-page prompt (page-keyed output, see output_schema)
)
MODEL_ROLE = "sonnet"
AVAILABILITY = ("received", "filed", "seen", "sent_to_clinician")
_FIELD_TYPES = {"date": ["string", "null"], "string": ["string", "null"], "boolean": ["boolean", "null"]}
_SHORT = 120
_SPAN = 4000
BATCH_MAX_TOKENS = 16000
# A page's structured answer averages ~1,000 output tokens (production, case of 900 pages). A batch
# whose answer hits BATCH_MAX_TOKENS is cut off, is invalid JSON and is asked again, so the whole
# batch fails. 10 pages leaves ~1.5x headroom; the configured size can only lower it.
MAX_PAGES_PER_CALL = 10


def pages_per_call(configured: int, limit: int | None = None) -> int:
    """Pages in one call: the smallest of the configured size, the model provider's own `limit` (when the kit
    gives one) and `MAX_PAGES_PER_CALL`, which keeps a Bedrock-sized answer inside its output cap."""
    if configured > MAX_PAGES_PER_CALL:
        logger.warning("extraction batch size %d is above the output limit; using %d", configured, MAX_PAGES_PER_CALL)
    return max(1, min(configured, MAX_PAGES_PER_CALL, limit if limit is not None else MAX_PAGES_PER_CALL))


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


def task_for(pack: MergedPack, max_tokens: int = BATCH_MAX_TOKENS) -> AiTask:
    return AiTask(
        task_id=TASK_ID,
        prompt_version=PROMPT_VERSION,
        model=MODEL_ROLE,
        system=_system(),
        schema=output_schema(pack),
        max_tokens=max_tokens,
        mends_labels=True,
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
    page (`positions`: word id to its number, from 1) and the first is not after the last. An entry that
    overlaps the one before it is trimmed to start at the word after it, so the earlier entry keeps its
    words and the later one keeps the rest; one with nothing left is dropped. Nothing about a dropped entry
    is logged but the count."""
    kinds = {k["key"] for k in pack.entry_kinds()}
    roles = {r["key"] for r in pack.author_roles()}
    specialties = {s["key"] for s in pack.entry_specialties()}
    word_at = {n: word_id for word_id, n in positions.items()}
    valid: list[tuple[int, int, Mapping[str, Any]]] = []
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
        )
        if not ok:
            rejected += 1
            continue
        assert first is not None and last is not None
        valid.append((first, last, item))
    kept: list[Entry] = []
    reached = 0  # the last word number an entry already kept covers
    for first, last, item in sorted(valid, key=lambda v: (v[0], v[1])):
        first = max(first, reached + 1)
        if first > last:
            rejected += 1
            continue
        reached = last
        kept.append(
            Entry(
                kind=str(item["kind"]),
                first_word_id=word_at[first],
                last_word_id=str(item["last_word_id"]),
                first_n=first,
                last_n=last,
                author_role=item.get("author_role"),
                specialty=item.get("specialty"),
            )
        )
    return SortedEntries(kept=kept, rejected=rejected)


def _without_births(found: Sequence[dates.Printed], births: Collection[dt.date]) -> list[dates.Printed]:
    return [d for d in found if not dates.is_birth(d, births)]


def page_births(
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    pack: MergedPack,
    births: Collection[dt.date],
    anchor: dt.date | None,
) -> tuple[set[dt.date], set[str]]:
    """The page's birth dates (the recorded ones and every date printed after a birth label) and the words
    of the labelled ones, which never date anything."""
    ids, labelled = dates.labelled_births([(w, t) for w, t in ordered if w not in struck], pack, anchor)
    return {*births, *labelled}, ids


def _dated_inside(
    inside: Sequence[tuple[str, str]],
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    pack: MergedPack,
    births: Collection[dt.date],
    anchor: dt.date | None,
) -> list[dates.Printed]:
    """The dates printed in `inside` that can date an entry: no birth date (recorded or found by its label), and
    with an `anchor` a two-digit year is read in its century (it stays ambiguous when no century fits)."""
    every, labelled_words = page_births(ordered, struck, pack, births, anchor)
    found = [d for d in dates.find(inside, pack) if not labelled_words.intersection(d.word_ids)]
    found = _without_births(found, every)
    if anchor is None:
        return found
    earliest = min(every) if every else None
    return [dates.resolve_century(d, anchor, earliest) for d in found]


def _choose(found: Sequence[dates.Printed]) -> dt.date | None:
    """The one date that reads a single way, when exactly one distinct such date is printed."""
    readable = {d.value for d in found if not d.ambiguous}
    return next(iter(readable)) if len(readable) == 1 else None


def _on_first_line(
    found: Sequence[dates.Printed], inside: Sequence[tuple[str, str]], lines: Mapping[str, int] | None
) -> list[dates.Printed]:
    """The dates that start on the entry's first printed line (none without line positions)."""
    if lines is None or not inside or inside[0][0] not in lines:
        return []
    first_line = lines[inside[0][0]]
    return [d for d in found if lines.get(d.word_ids[0]) == first_line]


def _starts_page(entry: Entry, page_size: int | None) -> bool:
    """Whether the entry holds the page's first word, and so its header (known only with the page's size)."""
    return page_size is not None and entry.first_n == 1


def _only_in_footer(
    chosen: dt.date,
    found: Sequence[dates.Printed],
    entry: Entry,
    ordered: Sequence[tuple[str, str]],
    lines: Mapping[str, int] | None,
    page_size: int | None,
) -> bool:
    """Whether an entry that reaches the page's end, and has lines of its own before the last, takes `chosen`
    only from the page's last printed line: that line may be a footer, not the entry's own date."""
    if lines is None or page_size is None or entry.last_n < page_size or not ordered:
        return False
    last_line, first_line = lines.get(ordered[-1][0]), lines.get(entry.first_word_id)
    if last_line is None or first_line is None or first_line == last_line:
        return False
    sources = [d for d in found if d.value == chosen]
    return bool(sources) and all(lines.get(w) == last_line for d in sources for w in d.word_ids)


def entry_date(
    entry: Entry,
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    pack: MergedPack,
    dates_of_birth: Collection[dt.date] = (),
    lines: Mapping[str, int] | None = None,
    page_size: int | None = None,
    anchor: dt.date | None = None,
) -> tuple[Any, str | None]:
    """An entry's date from the dates printed inside its words, by these rules in order:

    1. a birth date is never an entry's date: neither a recorded one nor a date printed directly after a birth
       label (the pack's words), with or without a recorded date of birth;
    2. exactly one distinct date that reads a single way is used;
    3. else exactly one such date on the entry's first printed line is used;
    4. else, when dates were printed but none can be chosen, the reader is asked to check (`please_check`);
    5. else the entry has no date.

    Nothing is guessed. With an `anchor` a two-digit year is read in the latest century not after it and not
    before the date of birth when known (basis `printed_century`) and then counts as any other date. A date that
    still reads more than one way (no `anchor`, or no century fits) is never chosen, and while one is printed
    anywhere in the entry no other date is chosen either (rules 2 and 3 do not apply).

    With `page_size` (the page's word count) page furniture is guarded, since entries cover every word: the
    entry holding the page's first word may hold a header, so rule 3 is not used for it; and a date taken only
    from the last printed line by an entry that reaches the page's end, and has lines before it, may be a
    footer, so it is not chosen. Both leave the entry to `please_check`."""
    inside = [
        (w, t) for n, (w, t) in enumerate(ordered, start=1) if entry.first_n <= n <= entry.last_n and w not in struck
    ]
    found = _dated_inside(inside, ordered, struck, pack, set(dates_of_birth), anchor)
    chosen = None
    if not any(d.ambiguous for d in found):
        chosen = _choose(found)
        if chosen is None and not _starts_page(entry, page_size):
            chosen = _choose(_on_first_line(found, inside, lines))
        if chosen is not None and _only_in_footer(chosen, found, entry, ordered, lines, page_size):
            chosen = None
    if chosen is not None:
        from_century = all(d.century for d in found if d.value == chosen)
        return chosen, dates.PRINTED_CENTURY if from_century else dates.PRINTED
    return (None, dates.PLEASE_CHECK) if found else (None, None)


def repair_entry_date(
    entry: Entry,
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    lines: Mapping[str, int],
    pack: MergedPack,
    *,
    dates_of_birth: Collection[dt.date],
    anchor: dt.date,
    page_size: int | None = None,
    page_entries: Sequence[Entry] = (),
) -> tuple[Any, str | None]:
    """The repair pass for an entry that has no date: the rules of `entry_date` (which read a two-digit year in
    the century before `anchor`, `printed_century`), then the date on the line directly above the entry
    (`line_above`). A known date of birth is never used, and a date inside any of `page_entries` (the page's
    entries) is never borrowed from the line above. With no date chosen the basis is `please_check` when
    dates were printed, else None. `page_size` guards page furniture as in `entry_date`."""
    births = set(dates_of_birth)
    when, basis = entry_date(entry, ordered, struck, pack, births, lines, page_size, anchor)
    if when is not None:
        return when, basis
    inside = [
        (w, t) for n, (w, t) in enumerate(ordered, start=1) if entry.first_n <= n <= entry.last_n and w not in struck
    ]
    found = _dated_inside(inside, ordered, struck, pack, births, anchor)
    if found:  # dates were printed but none can be chosen: the reader checks; nothing above is borrowed
        return when, basis
    above, above_basis, _ = date_repair.line_above_of(
        [entry.first_word_id],
        ordered,
        struck,
        lines,
        pack,
        anchor=anchor,
        skip=page_births(ordered, struck, pack, births, anchor)[0],
        blocked=[(e.first_n, e.last_n) for e in page_entries],
    )
    if above is not None:
        return above, above_basis
    return when, basis


def split_at_dated_lines(
    entry: Entry,
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    lines: Mapping[str, int] | None,
    pack: MergedPack,
    dates_of_birth: Collection[dt.date] = (),
    anchor: dt.date | None = None,
) -> list[Entry]:
    """One entry per dated line, for an entry that holds two or more lines that start with a printed date (a
    handwritten card with a line for each visit). Nothing is read by a model: the pieces cover the original
    entry's words exactly, in order, so provenance is unchanged. The first piece also keeps any lines before
    the first dated line, and a line that does not start with a date stays with the dated line above it.

    A birth date never starts an entry, and two dated lines in a row with the same date are one visit, so they
    stay together. Without line positions nothing is split."""
    if lines is None:
        return [entry]
    inside = [
        (w, t) for n, (w, t) in enumerate(ordered, start=1) if entry.first_n <= n <= entry.last_n and w not in struck
    ]
    first_of_line: dict[int, str] = {}
    for word_id, _ in inside:
        if word_id in lines:
            first_of_line.setdefault(lines[word_id], word_id)
    position = {w: n for n, (w, _) in enumerate(ordered, start=1)}
    starts: list[tuple[int, dt.date]] = []
    for d in sorted(
        _dated_inside(inside, ordered, struck, pack, set(dates_of_birth), anchor), key=lambda x: position[x.word_ids[0]]
    ):
        word_id = d.word_ids[0]
        if word_id not in lines or first_of_line.get(lines[word_id]) != word_id:
            continue
        if starts and starts[-1][1] == d.value:
            continue
        starts.append((position[word_id], d.value))
    if len(starts) < 2:
        return [entry]
    cuts = [entry.first_n, *(n for n, _ in starts[1:])]
    ends = [*(n - 1 for n in cuts[1:]), entry.last_n]
    return [
        replace(entry, first_word_id=ordered[a - 1][0], last_word_id=ordered[b - 1][0], first_n=a, last_n=b)
        for a, b in zip(cuts, ends, strict=True)
    ]
