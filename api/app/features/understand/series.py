"""Series on a lane (docs/design/timeline-v2-followups.md, item 5): entries the lane's pack rule (`series` in
lanes.yaml) draws as one bar. Filled in by the series stream; until then, none.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.packs import MergedPack
from app.features.understand import repo
from app.features.understand.timeline_models import Series


@dataclass
class LaneSeries:
    bars: list[Series] = field(default_factory=list)
    keys: dict[uuid.UUID, str] = field(default_factory=dict)


def build(pack: MergedPack, lane_key: str, facts: Sequence[repo.SeenFact]) -> LaneSeries:
    """The bars on one lane and, for each fact in a bar, its series key."""
    return LaneSeries()
