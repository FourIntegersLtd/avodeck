"""The ribbon's whole-record overview (docs/design/timeline-v2-followups.md, item 1): every dated entry counted
per lane per month, plus a pin for each entry with an open thing to look at or a linked question.
Lanes, their order, labels and colours come from the case's pack (rule 11); suggestions on pages the person has
not read stay hidden (rule 3, `repo.visible_facts`).
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date

from fastapi import Request
from psycopg import AsyncConnection

from app.core.packs import MergedPack
from app.features.understand import repo
from app.features.understand.lanes import arrange
from app.features.understand.timeline_models import Overview, OverviewLane, Pin, Range


def _months(first: date, last: date) -> list[str]:
    out: list[str] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


async def build(
    request: Request, conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack, as_seen_on: date | None
) -> Overview:
    seen = await repo.visible_facts(request, conn, case_id)
    facts = [
        f
        for f in seen
        if f.event_date is not None
        and (as_seen_on is None or (f.available_at is not None and f.available_at <= as_seen_on))
    ]
    links = await repo.linked_questions(conn, case_id)
    flagged = await repo.flagged_facts(conn, case_id)
    placed, _ = arrange(facts, pack.lanes(), kind=lambda f: f.kind, fields=lambda f: f.fields)
    days = [f.event_date for f in facts if f.event_date is not None]
    if not days:
        return Overview(lanes=[OverviewLane(key=k, label=_label(pack, k), counts=[]) for k, _ in placed])
    first, last = min(days), max(days)
    months = _months(first, last)
    index = {m: i for i, m in enumerate(months)}
    colour_of = {str(s["key"]): s.get("colour") for s in pack.source_types()}
    lanes: list[OverviewLane] = []
    pins: list[tuple[date, int, Pin]] = []
    for order, (key, got) in enumerate(placed):
        counts = [0] * len(months)
        for f in got:
            assert f.event_date is not None
            counts[index[f.event_date.strftime("%Y-%m")]] += 1
            if f.fact_id in flagged or links.get(f.fact_id):
                pin = Pin(
                    fact_id=f.fact_id,
                    date=f.event_date,
                    lane_key=key,
                    has_finding=f.fact_id in flagged,
                    question_ids=links.get(f.fact_id, []),
                )
                pins.append((f.event_date, order, pin))
        tally = Counter(f.source for f in got if f.source in colour_of)
        colour = colour_of[tally.most_common(1)[0][0]] if tally else None
        lanes.append(OverviewLane(key=key, label=_label(pack, key), colour=colour, counts=counts))
    pins.sort(key=lambda p: (p[0], p[1]))
    return Overview(range=Range(first=first, last=last), months=months, lanes=lanes, pins=[p for _, _, p in pins])


def _label(pack: MergedPack, key: str) -> str:
    return str(pack.vocabulary.get("lanes", {}).get(key, key))
