"""The foundation's daily schedules (run by the worker, keyed by date, F28).

`expire_uploads`: uploads left unfinished past `expires_at` become `expired` (state-machines.md
"Upload"), each with an `upload.expired` audit row, and their chunks are removed from Storage.
A date whose sweep fails is rolled back and tried again on the next catch-up.
"""

from __future__ import annotations

import uuid
from datetime import date

from psycopg import AsyncConnection

from app.core.config import ConfigError
from app.core.jobs import registry
from app.core.storage import SupabaseStorage, storage_settings, upload_prefix

UPLOAD_SWEEP = "expire_uploads"


async def expire_uploads(conn: AsyncConnection, run_date: date, storage: SupabaseStorage | None) -> list[uuid.UUID]:
    """Expire what was due by the end of `run_date`; returns the expired upload ids."""
    cur = await conn.execute("select expired_case_id, expired_upload_id from app.expire_uploads(%s)", (run_date,))
    rows = await cur.fetchall()
    if storage is not None:
        for case_id, upload_id in rows:
            await storage.delete(await storage.list(upload_prefix(case_id, upload_id)))
    return [upload_id for _, upload_id in rows]


@registry.schedule(UPLOAD_SWEEP)
async def sweep_uploads(conn: AsyncConnection, run_date: date) -> None:
    try:
        storage: SupabaseStorage | None = SupabaseStorage(storage_settings())
    except ConfigError:
        storage = None  # no Storage in this cell: nothing was stored, only the rows expire
    try:
        await expire_uploads(conn, run_date, storage)
    finally:
        if storage is not None:
            await storage.aclose()
