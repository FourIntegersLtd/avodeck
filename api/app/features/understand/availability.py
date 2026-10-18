"""When each fact reached the clinician (`available_at`), computed by code (architecture section 6).

On each page, items are taken in reading order. Availability marks (received, seen, filed, sent on)
belong to the run of arriving facts just before them, or, when no run comes before, to the arriving
facts after them. The clinician's own entries break runs. An arriving fact takes its earliest
mark; with none it is unknown, never its own date. Own-record entries count from their own date.
The latest override wins, and may say "not known".
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Mark:
    page_id: uuid.UUID
    kind: str
    date: date | None
    position: int


@dataclass(frozen=True)
class Placed:
    fact_id: uuid.UUID
    page_id: uuid.UUID
    position: int
    kind: str
    event_date: date | None
    own_record: bool


@dataclass(frozen=True)
class Override:
    fact_id: uuid.UUID
    available_at: date | None
    unknown: bool
    created_at: datetime


@dataclass(frozen=True)
class Availability:
    available_at: date | None
    basis: str | None


def _arrives(fact: Placed, arriving_kinds: Collection[str]) -> bool:
    return fact.kind in arriving_kinds or not fact.own_record


def _attach(facts: list[Placed], marks: list[Mark], arriving_kinds: Collection[str]) -> dict[uuid.UUID, list[Mark]]:
    items: list[tuple[int, int, Placed | Mark]] = [(f.position, 0, f) for f in facts]
    items += [(m.position, 1, m) for m in marks if m.date is not None]
    attached: dict[uuid.UUID, list[Mark]] = defaultdict(list)
    run: list[Placed] = []
    forward: list[Mark] = []
    last_was_mark = False
    for _, _, item in sorted(items, key=lambda i: (i[0], i[1])):
        if isinstance(item, Mark):
            if run:
                for fact in run:
                    attached[fact.fact_id].append(item)
            else:
                forward.append(item)
            last_was_mark = True
        elif _arrives(item, arriving_kinds):
            if last_was_mark:
                run = []
            run.append(item)
            attached[item.fact_id].extend(forward)
            last_was_mark = False
        else:
            run, forward, last_was_mark = [], [], False
    return attached


def compute(
    placed: Iterable[Placed],
    marks: Iterable[Mark],
    arriving_kinds: Collection[str],
    overrides: Iterable[Override],
) -> dict[uuid.UUID, Availability]:
    facts = list(placed)
    by_page: dict[uuid.UUID, list[Placed]] = defaultdict(list)
    for fact in facts:
        by_page[fact.page_id].append(fact)
    marks_by_page: dict[uuid.UUID, list[Mark]] = defaultdict(list)
    for mark in marks:
        marks_by_page[mark.page_id].append(mark)
    attached: dict[uuid.UUID, list[Mark]] = {}
    for page_id, page_facts in by_page.items():
        attached.update(_attach(page_facts, marks_by_page[page_id], arriving_kinds))

    latest: dict[uuid.UUID, Override] = {}
    for override in overrides:
        current = latest.get(override.fact_id)
        if current is None or override.created_at > current.created_at:
            latest[override.fact_id] = override

    out: dict[uuid.UUID, Availability] = {}
    for fact in facts:
        chosen = latest.get(fact.fact_id)
        if chosen is not None:
            out[fact.fact_id] = (
                Availability(None, "override_unknown")
                if chosen.unknown
                else Availability(chosen.available_at, "override")
            )
        elif not _arrives(fact, arriving_kinds):
            out[fact.fact_id] = Availability(fact.event_date, "own_record" if fact.event_date else None)
        elif found := attached.get(fact.fact_id):
            first = min(found, key=lambda m: m.date or date.max)
            out[fact.fact_id] = Availability(first.date, first.kind)
        else:
            out[fact.fact_id] = Availability(None, None)
    return out
