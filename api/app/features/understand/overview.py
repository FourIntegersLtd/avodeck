"""The ribbon's whole-record overview (docs/design/timeline-v2-followups.md, item 1). Filled in by the ribbon
stream; until then, an empty overview (the web app still draws the ribbon from `/timeline`).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import Request
from psycopg import AsyncConnection

from app.core.packs import MergedPack
from app.features.understand.timeline_models import Overview


async def build(
    request: Request, conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack, as_seen_on: date | None
) -> Overview:
    return Overview()
