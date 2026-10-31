"""The foundation's daily schedules (run by the worker, keyed by date, F28).

`expire_uploads`: uploads left unfinished past `expires_at` become `expired` (state-machines.md
"Upload"), each with an `upload.expired` audit row, and their chunks are removed from Storage.
Terminal rows (expired/cancelled/failed) with `cleaned_at is null` are also returned so their
Storage objects can be deleted and `cleaned_at` set (R8). A date whose sweep fails is rolled
back and tried again on the next catch-up.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from psycopg import AsyncConnection

from app.core.config import ConfigError
from app.core.jobs import registry
from app.core.storage import SupabaseStorage, storage_settings, upload_prefix

UPLOAD_SWEEP = "expire_uploads"
_log = logging.getLogger(__name__)


async def expire_uploads(conn: AsyncConnection, run_date: date, storage: SupabaseStorage | None) -> list[uuid.UUID]:
    """Expire what was due by the end of `run_date`, clean terminal rows; returns affected upload ids."""
    cur = await conn.execute(
        "select expired_case_id, expired_upload_id, expired_status from app.expire_uploads(%s)", (run_date,)
    )
    rows = await cur.fetchall()
    cleaned: list[uuid.UUID] = []
    if storage is not None:
        for case_id, upload_id, _status in rows:
            try:
                paths = await storage.list(upload_prefix(case_id, upload_id))
                if paths:
                    await storage.delete(paths)
                await conn.execute("select app.mark_upload_cleaned(%s)", (upload_id,))
                cleaned.append(upload_id)
            except Exception:  # noqa: BLE001
                _log.warning("upload cleanup failed, will retry", extra={"upload_id": str(upload_id)})
    return [upload_id for _, upload_id, _ in rows]


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
