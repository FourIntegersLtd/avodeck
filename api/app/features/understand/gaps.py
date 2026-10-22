"""Gaps on the timeline (docs/design/timeline-v2-followups.md, item 4): how long after an entry an open thing
to look at rests on, and what kind of wait it is. Filled in by the gaps stream; until then, none.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from psycopg import AsyncConnection

from app.core.packs import MergedPack
from app.features.understand import repo
from app.features.understand.timeline_models import Gap


async def build(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    pack: MergedPack,
    shown: Sequence[repo.SeenFact],
    visible: Sequence[repo.SeenFact],
) -> list[Gap]:
    """Gaps starting at an entry in `shown` (the timeline's window); `visible` is everything the person may see."""
    return []
