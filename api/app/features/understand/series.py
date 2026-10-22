"""Series on a lane (docs/design/timeline-v2-followups.md, item 5): entries the lane's pack rule (`series` in
lanes.yaml) draws as one bar. Which kinds, which fields must agree and how long a pause may be all come from the
pack (rule 11). A bar's label is the first issue's own words, copied, never written.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.packs import MergedPack
from app.features.understand import repo
from app.features.understand.timeline_models import Series


@dataclass
class LaneSeries:
    bars: list[Series] = field(default_factory=list)
    keys: dict[uuid.UUID, str] = field(default_factory=dict)


def _words(value: Any) -> str:
    return "" if value is None else str(value)


def _same(value: Any) -> str:
    return " ".join(_words(value).casefold().split())


def build(pack: MergedPack, lane_key: str, facts: Sequence[repo.SeenFact]) -> LaneSeries:
    """The bars on one lane and, for each fact in a bar, its series key."""
    lane = next((item for item in pack.lanes() if item["key"] == lane_key), None)
    rule: dict[str, Any] | None = lane.get("series") if lane else None
    if not rule:
        return LaneSeries()
    kinds, same = set(rule["kinds"]), list(rule["same"])
    groups: dict[tuple[str, ...], list[repo.SeenFact]] = defaultdict(list)
    for fact in facts:
        if fact.kind not in kinds or fact.event_date is None:
            continue
        norm = tuple(_same(fact.fields.get(name)) for name in same)
        if all(norm):
            groups[norm].append(fact)
    runs: list[list[repo.SeenFact]] = []
    for group in groups.values():
        group.sort(key=lambda f: (f.event_date, f.page_no))  # type: ignore[type-var, return-value]
        run = [group[0]]
        for fact in group[1:]:
            assert fact.event_date is not None and run[-1].event_date is not None
            if (fact.event_date - run[-1].event_date).days > rule["max_gap_days"]:
                runs.append(run)
                run = []
            run.append(fact)
        runs.append(run)
    kept = sorted((r for r in runs if len(r) >= rule["min_count"]), key=lambda r: (r[0].event_date, r[0].page_no))  # type: ignore[type-var, return-value]
    out = LaneSeries()
    for n, run in enumerate(kept, start=1):
        first, last = run[0].event_date, run[-1].event_date
        assert first is not None and last is not None
        key = f"{lane_key}:{n}"
        label = " ".join(_words(run[0].fields.get(name)) for name in same)
        out.bars.append(Series(key=key, label=label, first=first, last=last, count=len(run), fact_ids=[f.fact_id for f in run]))
        out.keys.update({f.fact_id: key for f in run})
    return out
