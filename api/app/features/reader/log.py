"""The read log behind the blind-read gate (`core/contracts/reading.PageReadLog`, rule 3).

`has_read` is the person's own read, so an assistant who has read a page sees its suggestions.
`read_page_ids` lists only reads that count for citing (the named expert's), so `citable_facts()`
never unlocks a fact through anyone else's reading.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.features.reader import repo


class ReaderPageReadLog:
    async def has_read(self, conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> bool:
        return await repo.has_read(conn, case_id, page_no, person_id)

    async def read_page_ids(self, conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> set[uuid.UUID]:
        return await repo.citing_read_page_ids(conn, case_id, person_id)
