"""One consultation as the clinician saw it (S12): what was available on the day.

Visible: available on or before the day. Later ones are kept apart ("What came later"). Ones with no
known arrival are listed as unknown and never placed before the day. Rejected facts are left out.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class SeenItem:
    fact_id: uuid.UUID
    available_at: date | None
    status: str


@dataclass
class AsSeen:
    visible: list[SeenItem] = field(default_factory=list)
    later: list[SeenItem] = field(default_factory=list)
    unknown: list[SeenItem] = field(default_factory=list)


def as_seen(on: date, items: Iterable[SeenItem], *, exclude: uuid.UUID | None) -> AsSeen:
    view = AsSeen()
    for item in items:
        if item.status == "rejected" or item.fact_id == exclude:
            continue
        if item.available_at is None:
            view.unknown.append(item)
        elif item.available_at <= on:
            view.visible.append(item)
        else:
            view.later.append(item)
    return view
