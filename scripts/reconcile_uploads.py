#!/usr/bin/env python3
"""Remove orphaned upload objects from private Storage, for every case (R8 safety net).

    poetry -P api run python scripts/reconcile_uploads.py

The daily `expire_uploads` sweep reconciles the cases it touched; this covers the rest (a crash between
an upload's first chunk and its row, objects that outlived a failed delete). It connects as the
restricted `app_worker` role on `DATABASE_URL_DIRECT` through `core/db.py`, one case at a time, and
uses the same `reconcile_upload_objects` rule: only certain orphans go. Prints counts only.
Needs DATABASE_URL_DIRECT, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (Storage only).
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from app.core.config import ConfigError, database_settings
from app.core.db import Database
from app.core.housekeeping import reconcile_upload_objects
from app.core.storage import SupabaseStorage, storage_settings


async def run() -> int:
    settings = database_settings()
    if not settings.url_direct:
        raise ConfigError("DATABASE_URL_DIRECT is not set")
    database = Database(settings.url_direct, max_size=2)
    storage = SupabaseStorage(storage_settings())
    cases = deleted = left = failed = 0
    await database.open()
    try:
        for name in await storage.list_folders("cases"):
            try:
                case_id = uuid.UUID(name)
            except ValueError:
                continue
            if str(case_id) != name:
                continue
            async with database.job(case_id) as conn:
                result = await reconcile_upload_objects(conn, storage, case_id)
            cases += 1
            deleted, left, failed = deleted + result.deleted, left + result.left, failed + result.failed
    finally:
        await storage.aclose()
        await database.close()
    print(f"cases={cases} deleted={deleted} left={left} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
