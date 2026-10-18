"""Slice B's Today items (screen 03, `core/contracts/today.py`): keys and counts only (rule 6).

- `reader_pages_to_read`: per case where the person is the named expert and pages are left to read, with
  the time left from their own pace. Opens screen 09.
- `reader_pages_to_type`: per case where the person is the named expert and unreadable pages are neither
  typed out nor sent for a clearer copy. Opens screen 10.
"""

from __future__ import annotations

import math
import uuid

from psycopg import AsyncConnection

from app.core.contracts.today import CaseCard, NeedsYouItem, needs_you
from app.features.reader import repo


async def _mine(conn: AsyncConnection, cards: list[CaseCard]) -> list[uuid.UUID]:
    cur = await conn.execute(
        "select id from app.cases where id = any(%s) and named_expert_id = app.current_person_id()",
        ([c.case_id for c in cards],),
    )
    mine = {r[0] for r in await cur.fetchall()}
    return [c.case_id for c in cards if c.case_id in mine]


@needs_you.provider("reader_pages_to_read")
async def pages_to_read(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    cur = await conn.execute("select app.current_person_id()")
    row = await cur.fetchone()
    assert row is not None
    items = []
    for case_id in await _mine(conn, cards):
        read, total, seconds_left = await repo.progress(conn, case_id, row[0])
        if read < total:
            items.append(
                NeedsYouItem(
                    kind="reader_pages_to_read",
                    case_id=case_id,
                    rank=30,
                    label_key="reader.today.pages_to_read",
                    action_key="reader.today.pages_to_read_action",
                    screen="S09",
                    count=total - read,
                    minutes=max(1, math.ceil(seconds_left / 60)),
                )
            )
    return items


@needs_you.provider("reader_pages_to_type")
async def pages_to_type(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    return [
        NeedsYouItem(
            kind="reader_pages_to_type",
            case_id=case_id,
            rank=35,
            label_key="reader.today.pages_to_type",
            action_key="reader.today.pages_to_type_action",
            screen="S10",
            count=count,
        )
        for case_id, count in await repo.pages_to_type(conn, await _mine(conn, cards))
    ]
