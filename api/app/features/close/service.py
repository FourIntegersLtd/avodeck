"""Closing a case: settle, archive, reopen, retention, legal holds, export download (architecture 10.12).

Retention (the years come from the case's packs, never from here):
- `keep_default`: deleted the pack's retention years after settlement.
- `return_then_delete`: due on the settlement date (the records go back, then the case is deleted).
- `delete_now`: due today and `delete_case` is queued; only for a case never served and not held.
The clock runs only once the case is closed (settled, archived or withdrawn). Settlement is the
base; a case closed without settling (withdrawn) counts from the day it was withdrawn, so archiving
it or changing its policy later never moves the date. Reopening stops the clock and keeps the chosen
policy for the next settlement; a case already due for deletion cannot be reopened.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any

from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import CaseAccess, check_case_access
from app.core.contracts.invoices import case_invoice_rows
from app.core.contracts.reading import page_read_log, verification_counts
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError, conflict
from app.core.jobs import registry
from app.core.packs import PackLibrary
from app.core.storage import StorageError, SupabaseStorage, case_prefix
from app.features.close import repo
from app.features.close.schemas import (
    EXPORT_KINDS,
    EXPORT_KINDS_BY_DEFAULT,
    CloseSummary,
    ExportFile,
    ExportFiles,
    Hold,
)

CLOSED = ("settled", "archived", "withdrawn")
# The download is sent in pieces of this size. CaseCrypto opens a file whole (one AES-GCM seal), so
# the decrypted ZIP is held in memory once; it is never copied into a single response body.
DOWNLOAD_CHUNK = 1024 * 1024
DEFAULT_POLICY = "keep_default"


def plus_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February in a year without one
        return day.replace(year=day.year + years, day=28)


def retention_years(library: PackLibrary, case: dict[str, Any]) -> int:
    return int(library.combine(case["jurisdiction"], case["case_type"], case["specialty"]).retention_years)


def delete_after(policy: str | None, status: str, base: date | None, today: date, years: int) -> date | None:
    """When the case is due for deletion under `policy`, or None while the clock is not running."""
    if policy == "delete_now":
        return today
    if policy is None or status not in CLOSED:
        return None
    start = base or today
    return plus_years(start, years) if policy == "keep_default" else start


def _held(holds: list[dict[str, Any]], today: date) -> bool:
    return any(h["until"] is None or h["until"] >= today for h in holds)


async def summary(
    conn: AsyncConnection, library: PackLibrary, case_id: uuid.UUID, *, job_id: uuid.UUID | None = None
) -> CloseSummary:
    case = await repo.case(conn, case_id)
    holds = await repo.active_holds(conn, case_id)
    cert = await repo.deletion(conn, case_id)
    return CloseSummary(
        case_id=case_id,
        status=case["status"],
        settled_at=case["settled_at"],
        retention_policy=case["retention_policy"],
        delete_after=case["delete_after"],
        retention_years=retention_years(library, case),
        ever_served=case["ever_served"],
        holds=[Hold(**h) for h in holds],
        held=_held(holds, await repo.today(conn)),
        deletion_started=await repo.deletion_started(conn, case_id),
        deleted_at=cert["deleted_at"] if cert else None,
        job_id=job_id,
    )


async def _locked_case(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, Any]:
    """The case row, locked; refused once its deletion run has started."""
    case = await repo.case(conn, case_id, lock=True)
    if case["status"] == "deleted" or await repo.deletion_started(conn, case_id):
        raise conflict()
    return case


def _base(case: dict[str, Any]) -> date | None:
    """When the case closed: its settlement, else the day it was withdrawn (None while open)."""
    if case["settled_at"] is not None:
        return case["settled_at"].date()  # type: ignore[no-any-return]
    return case["withdrawn_on"]  # type: ignore[no-any-return]


async def _record(conn: AsyncConnection, case_id: uuid.UUID, action: str, target: str | None = None) -> None:
    await audit.record(conn, action=action, target=target or f"case:{case_id}", case_id=case_id)


async def settle(conn: AsyncConnection, library: PackLibrary, case_id: uuid.UUID) -> None:
    case = await _locked_case(conn, case_id)
    if case["status"] not in ("active", "served"):
        raise conflict()
    today = await repo.today(conn)
    policy = case["retention_policy"] or DEFAULT_POLICY
    due = delete_after(policy, "settled", today, today, retention_years(library, case))
    await repo.update_case(conn, case_id, status="settled", retention_policy=policy, delete_after=due)
    await _record(conn, case_id, "case.settled")


async def archive(conn: AsyncConnection, library: PackLibrary, case_id: uuid.UUID) -> None:
    case = await _locked_case(conn, case_id)
    if case["status"] not in ("settled", "withdrawn"):
        raise conflict()
    policy = case["retention_policy"] or DEFAULT_POLICY
    today = await repo.today(conn)
    due = delete_after(policy, "archived", _base(case), today, retention_years(library, case))
    await repo.update_case(conn, case_id, status="archived", retention_policy=policy, delete_after=due)
    await _record(conn, case_id, "case.archived")


async def reopen(conn: AsyncConnection, case_id: uuid.UUID) -> None:
    case = await _locked_case(conn, case_id)
    if case["status"] not in ("settled", "archived"):
        raise conflict()
    if case["delete_after"] is not None and case["delete_after"] <= await repo.today(conn):
        raise conflict()  # due for deletion: the daily run may already have queued it
    await repo.update_case(conn, case_id, status="active", delete_after=None)
    await _record(conn, case_id, "case.reopened")


async def change_retention(
    conn: AsyncConnection, library: PackLibrary, case_id: uuid.UUID, policy: str
) -> uuid.UUID | None:
    """Sets the policy and recalculates `delete_after`; for `delete_now`, returns the queued job."""
    if policy == "delete_now":
        await check_case_access(conn, case_id, "case.delete_now")
    case = await _locked_case(conn, case_id)
    today = await repo.today(conn)
    if policy == "delete_now" and (case["ever_served"] or _held(await repo.active_holds(conn, case_id), today)):
        raise conflict()
    due = delete_after(policy, case["status"], _base(case), today, retention_years(library, case))
    await repo.update_case(conn, case_id, retention_policy=policy, delete_after=due)
    await _record(conn, case_id, "case.retention_changed", f"retention:{policy}")
    if policy == "delete_now":
        return await registry.enqueue(conn, case_id, "delete_case")
    return None


async def set_hold(conn: AsyncConnection, case_id: uuid.UUID, reason: str, until: date | None) -> Hold:
    hold_id = await repo.add_hold(conn, case_id, reason, until)  # refused (409) once deletion has started
    await _record(conn, case_id, "legal_hold.set", f"legal_hold:{hold_id}")
    row = await repo.hold(conn, case_id, hold_id)
    assert row is not None
    return Hold(**row)


async def release_hold(conn: AsyncConnection, case_id: uuid.UUID, hold_id: uuid.UUID) -> Hold:
    row = await repo.hold(conn, case_id, hold_id)
    if row is None:
        raise ApiError("not_found", "Not found.", 404)
    if row["released_at"] is not None:
        raise conflict()
    await repo.release_hold(conn, hold_id)
    await _record(conn, case_id, "legal_hold.released", f"legal_hold:{hold_id}")
    row = await repo.hold(conn, case_id, hold_id)
    assert row is not None
    return Hold(**row)


def selection_path(case_id: uuid.UUID, job_id: uuid.UUID) -> str:
    """Where the chosen kinds wait for the job (jobs carry no payload). Under the case prefix, so
    a deletion run sweeps it; the job removes it once read."""
    return f"{case_prefix(case_id)}/exports/{job_id}/selection.json"


async def export_files(conn: AsyncConnection, case_id: uuid.UUID) -> ExportFiles:
    """The kinds of file an export can hold, with how many items each has."""
    cur = await conn.execute(
        "select count(*) from app.report_versions"
        " where case_id = %s and (pdf_path is not null or docx_path is not null)",
        (case_id,),
    )
    row = await cur.fetchone()
    counts = {
        "reports": int(row[0]) if row else 0,
        "method": sum((await verification_counts(conn, case_id)).values()),
        "reads": len(await page_read_log(conn, case_id)),
        "invoice": len(await case_invoice_rows(conn, case_id)),
    }
    return ExportFiles(
        files=[ExportFile(kind=k, count=counts[k], selected=k in EXPORT_KINDS_BY_DEFAULT) for k in EXPORT_KINDS]
    )


async def request_export(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    kinds: Sequence[str] | None = None,
    storage: SupabaseStorage | None = None,
    crypto: CaseCrypto | None = None,
) -> uuid.UUID:
    await _locked_case(conn, case_id)  # refused once deletion has started
    job_id = await registry.enqueue(conn, case_id, "export_case")
    if kinds and storage and crypto:
        path = selection_path(case_id, job_id)
        await storage.put(path, await crypto.encrypt(conn, case_id, json.dumps(sorted(kinds)).encode(), purpose=path))
    await _record(conn, case_id, "case.export_requested", f"export:{job_id}")
    return job_id


def export_variant(access: CaseAccess) -> str:
    """The named expert gets the full record of work; an organisation admin the administrative copy."""
    return "full" if access.case_role == "named_expert" else "admin"


def export_path(case_id: uuid.UUID, job_id: uuid.UUID, variant: str) -> str:
    return f"{case_prefix(case_id)}/exports/{job_id}/{variant}.zip"


async def check_export_done(conn: AsyncConnection, case_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """404 unless `job_id` is an export of this case; 409 until it has finished; then 404 unless the
    caller asked for it (only the requester's copy is built)."""
    job = await repo.export_job(conn, case_id, job_id)
    if job is None:
        raise ApiError("not_found", "Not found.", 404)
    if job["status"] != "done":
        raise conflict()
    if not job["mine"]:
        raise ApiError("not_found", "Not found.", 404)


async def download_export(
    conn: AsyncConnection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    access: CaseAccess,
    job_id: uuid.UUID,
) -> bytes:
    """The export ZIP, once: the stored copy is removed as it is handed over, and the download audited."""
    case_id = access.case_id
    path = export_path(case_id, job_id, export_variant(access))
    await repo.lock_download(conn, path)
    try:
        blob = await storage.get(path)
    except StorageError:
        raise ApiError("not_found", "This export has already been downloaded.", 404) from None
    data = await crypto.decrypt(conn, case_id, blob, purpose=path)
    await _record(conn, case_id, "case.export_downloaded", f"export:{job_id}")
    await storage.delete([path])
    return data


def chunks(data: bytes, size: int = DOWNLOAD_CHUNK) -> Iterator[bytes]:
    view = memoryview(data)
    for start in range(0, len(view), size):
        yield bytes(view[start : start + size])
