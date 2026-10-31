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
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from psycopg import AsyncConnection

from app.core.config import ConfigError
from app.core.jobs import registry
from app.core.storage import SupabaseStorage, case_prefix, storage_settings, upload_prefix

UPLOAD_SWEEP = "expire_uploads"
_log = logging.getLogger(__name__)
ORPHAN_AGE = timedelta(days=8)
TERMINAL_WITHOUT_DOCUMENT = ("expired", "cancelled", "failed")


@dataclass(frozen=True)
class ReconcileResult:
    """Counts only: upload folders deleted, left alone, or failed (retried by the next run)."""

    deleted: int = 0
    left: int = 0
    failed: int = 0


async def expire_uploads(conn: AsyncConnection, run_date: date, storage: SupabaseStorage | None) -> list[uuid.UUID]:
    """Expire what was due by the end of `run_date`, clean terminal rows; returns affected upload ids."""
    return [upload_id for _, upload_id in await _expire_uploads(conn, run_date, storage)]


async def _expire_uploads(
    conn: AsyncConnection, run_date: date, storage: SupabaseStorage | None
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """As `expire_uploads`, as (case id, upload id) pairs."""
    cur = await conn.execute(
        "select expired_case_id, expired_upload_id, expired_status from app.expire_uploads(%s)", (run_date,)
    )
    rows = await cur.fetchall()
    for case_id, upload_id, status in rows:
        if status == "expired":  # ids only: no file name, no path (rule 6)
            _log.info("upload.expired", extra={"upload_id": str(upload_id), "case_id": str(case_id)})
    if storage is not None:
        for case_id, upload_id, _status in rows:
            prefix = upload_prefix(case_id, upload_id)
            try:
                paths = await storage.list(prefix)
                parts = len(paths)
                if paths:
                    await storage.delete(paths)
                    # Cleaned only once Storage no longer lists the parts (R8: after the deletion, not after a try).
                    if await storage.list(prefix):
                        _log.warning("upload parts still stored, will retry", extra={"upload_id": str(upload_id)})
                        continue
                # A savepoint per row: a database error on one row must not abort the sweep's own transaction.
                async with conn.transaction():
                    await conn.execute("select app.mark_upload_cleaned(%s)", (upload_id,))
                ids = {"upload_id": str(upload_id), "case_id": str(case_id)}
                _log.info("upload.cleaned", extra={**ids, "parts": parts})
            except Exception:  # noqa: BLE001
                _log.warning("upload cleanup failed, will retry", extra={"upload_id": str(upload_id)})
    return [(case_id, upload_id) for case_id, upload_id, _ in rows]


async def reconcile_upload_objects(
    conn: AsyncConnection, storage: SupabaseStorage, case_id: uuid.UUID
) -> ReconcileResult:
    """Delete a case's orphaned upload folders; leave everything not certainly orphaned.

    `conn` must be the worker's job connection for `case_id` (RLS shows only that case's upload rows);
    anything else gets a warning and zero counts, since hidden rows look exactly like orphans. A folder
    `cases/{case}/uploads/{name}/` is deleted only when `name` is a UUID and either its row is terminal
    without a `document_id` (then marked cleaned, once the delete is confirmed) or there is no row and
    every object in it is older than 8 days. A row with a `document_id` is never touched. Counts only are
    logged: never a path, a filename or any patient text.
    """
    root = f"{case_prefix(case_id)}/uploads"
    # A connection not scoped to this case sees no rows at all, so every folder would look row-less and
    # old enough to delete. Refuse to do anything unless the scoping is proven. `app.job_case_id` is a plain
    # session setting that any role can set (migration 0027), so the role is part of the proof: on `app_api`
    # the setting means nothing, every row is hidden, and each folder would look like an orphan.
    cur = await conn.execute("select session_user = 'app_worker' and app.current_job_case_id() = %s", (case_id,))
    scoped = await cur.fetchone()
    if scoped is None or scoped[0] is not True:
        _log.warning("upload reconcile skipped: the connection is not the worker scoped to this case")
        return ReconcileResult()
    cur = await conn.execute("select id, status, document_id from app.uploads where case_id = %s", (case_id,))
    rows = {upload_id: (status, document_id) for upload_id, status, document_id in await cur.fetchall()}
    # A completed document's bytes live at its upload prefix (documents.storage_path): never delete those.
    cur = await conn.execute(
        "select storage_path from app.documents where case_id = %s and storage_path is not null", (case_id,)
    )
    document_paths = [path for (path,) in await cur.fetchall()]
    cutoff = datetime.now(UTC) - ORPHAN_AGE
    deleted = left = failed = 0
    for name in await storage.list_folders(root):
        try:
            upload_id = uuid.UUID(name)
        except ValueError:
            upload_id = None
        if upload_id is None or str(upload_id) != name:
            left += 1
            continue
        row = rows.get(upload_id)
        prefix = f"{root}/{name}"
        # Checked for every folder, not only the row-less ones: if row visibility were ever wrong, this
        # alone still keeps a live document's bytes.
        if any(path == prefix or path.startswith(prefix + "/") for path in document_paths):
            left += 1
            continue
        try:
            if row is not None:
                status, document_id = row
                if document_id is not None or status not in TERMINAL_WITHOUT_DOCUMENT:
                    left += 1
                    continue
            else:
                times = await storage.list_object_times(prefix)
                if not times or any(t is None or t.tzinfo is None or t > cutoff for t in times.values()):
                    left += 1
                    continue
            paths = await storage.list_recursive(prefix)
            if paths:
                await storage.delete(paths)
                if await storage.list_recursive(prefix):
                    failed += 1
                    continue
            if row is not None:
                # A savepoint: a database error here must not abort the caller's transaction.
                async with conn.transaction():
                    await conn.execute("select app.mark_upload_cleaned(%s)", (upload_id,))
            if paths:
                deleted += 1
            else:
                left += 1
        except Exception:  # noqa: BLE001 - a timeout or connection error is not a StorageError; next run retries
            failed += 1
    _log.info("upload.reconciled", extra={"deleted": deleted, "left": left, "failed": failed})
    return ReconcileResult(deleted=deleted, left=left, failed=failed)


@registry.schedule(UPLOAD_SWEEP)
async def sweep_uploads(conn: AsyncConnection, run_date: date) -> None:
    try:
        storage: SupabaseStorage | None = SupabaseStorage(storage_settings())
    except ConfigError:
        storage = None  # no Storage in this cell: nothing was stored, only the rows expire
    try:
        touched = await _expire_uploads(conn, run_date, storage)
        if storage is not None:
            for case_id in sorted({case_id for case_id, _ in touched}, key=str):
                try:
                    # A savepoint, so a database error here cannot abort (and so roll back) the day's expiry.
                    async with conn.transaction():
                        # The same worker connection, scoped to one case the way `Database.job` scopes it.
                        await conn.execute("select set_config('app.job_case_id', %s, true)", (str(case_id),))
                        await reconcile_upload_objects(conn, storage, case_id)
                except Exception:  # noqa: BLE001
                    _log.warning("upload reconcile failed, will retry")
            await conn.execute("select set_config('app.job_case_id', '', true)")  # unscoped again
    finally:
        if storage is not None:
            await storage.aclose()
