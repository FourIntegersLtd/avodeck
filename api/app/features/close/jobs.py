"""Slice H jobs: `export_case` (the record of work as a ZIP), `delete_case` (deletion and its
certificate) and the daily close run (architecture 10.9 and 10.12; F20, F28, F29, F30).

Everything runs in this region cell's worker (rule 5). Logs carry exception types only (rule 6).
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import logging
import tempfile
import uuid
import zipfile
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.email import deletion_certificate_mail
from app.core.jobs import JobContext, JobRegistry, registry
from app.core.notifications import notify
from app.core.packs import PackLibrary, default_library
from app.core.storage import SupabaseStorage, case_prefix, storage_settings
from app.features.close.service import DEFAULT_POLICY, delete_after, export_path

logger = logging.getLogger("app.features.close")

DAILY = "close_daily"
# How long the database and Storage backups keep a deleted case (the certificate states the date).
BACKUP_RETENTION_DAYS = 30
# Settled cases move to the archive this many days after settlement.
ARCHIVE_AFTER_DAYS = 30
# Export copies nobody downloaded are removed this many days after they were made; each daily run
# looks back over the sweep window so a missed day is caught up.
EXPORT_KEEP_DAYS = 7
EXPORT_SWEEP_WINDOW_DAYS = 30
PAUSED_KIND = "deletion_paused"
VARIANTS = ("full", "admin")
# Parts of the record of work whose slices are not built yet: exported as labelled empty files.
NOT_YET_AVAILABLE = (
    ("logs/methodology.csv", "not_built_yet"),
    ("logs/page-reads.csv", "not_built_yet"),
    ("invoices.csv", "not_built_yet"),
    ("requests.csv", "not_built_yet"),
)


class _Services:
    """Storage and the case keys for the jobs, made from the cell config on first use."""

    def __init__(self) -> None:
        self.storage: SupabaseStorage | None = None
        self.crypto: CaseCrypto | None = None
        self.packs: PackLibrary | None = None

    def get_storage(self) -> SupabaseStorage:
        if self.storage is None:
            self.storage = SupabaseStorage(storage_settings())
        return self.storage

    def get_crypto(self) -> CaseCrypto:
        if self.crypto is None:
            self.crypto = CaseCrypto(key_provider_from_env())
        return self.crypto

    def get_packs(self) -> PackLibrary:
        if self.packs is None:
            self.packs = default_library()
        return self.packs


services = _Services()


async def _one(conn: AsyncConnection, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return await cur.fetchone()


async def _all(conn: AsyncConnection, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return await cur.fetchall()


# --- delete_case ------------------------------------------------------------------------------------


async def _held(conn: AsyncConnection, case: dict[str, Any]) -> bool:
    if case["on_hold"]:
        return True
    row = await _one(
        conn,
        "select 1 as held from app.legal_holds where case_id = %s and released_at is null"
        " and (until is null or until >= current_date)",
        (case["id"],),
    )
    return row is not None


async def _tell_paused(ctx: JobContext, conn: AsyncConnection, case: dict[str, Any]) -> None:
    """Tell the named expert the deletion is paused, once per hold (F30)."""
    told = await _one(
        conn,
        "select 1 as told from app.notifications n where n.case_id = %s and n.kind = %s and n.created_at >="
        " coalesce((select max(h.created_at) from app.legal_holds h where h.case_id = %s"
        " and h.released_at is null), '-infinity')",
        (case["id"], PAUSED_KIND, case["id"]),
    )
    if told is None:
        await notify(
            conn,
            ctx.mailer,
            person_id=case["named_expert_id"],
            organisation_id=case["organisation_id"],
            kind=PAUSED_KIND,
            case_id=case["id"],
        )


async def _start(ctx: JobContext, storage: SupabaseStorage) -> bool:
    """Writes the certificate row (deletion has started). False if the case is not to be deleted now."""
    async with ctx.transaction() as conn:
        case = await _one(
            conn,
            "select id, status, delete_after, on_hold, named_expert_id, organisation_id,"
            " delete_after <= current_date as due from app.cases where id = %s for update",
            (ctx.case_id,),
        )
        if case is None or case["status"] == "deleted":
            return False
        cert = await _one(conn, "select deleted_at from app.deletion_certificates where case_id = %s", (ctx.case_id,))
        if cert is not None:
            return cert["deleted_at"] is None  # resume a run that started; nothing to do once finished
        if not case["due"]:
            return False  # reopened, or the policy changed, since the job was queued
        if await _held(conn, case):
            await _tell_paused(ctx, conn, case)
            return False
        docs = await _all(
            conn, "select sha256 from app.documents where case_id = %s and sha256 is not null order by sha256",
            (ctx.case_id,),
        )  # fmt: skip
        reports = await _all(
            conn,
            "select sha256 from app.report_versions where case_id = %s and sha256 is not null order by version_no",
            (ctx.case_id,),
        )
        heads = await _one(conn, "select app.close_log_chain_heads(%s) as heads", (ctx.case_id,))
        objects = await storage.list_recursive(case_prefix(ctx.case_id))
        await conn.execute(
            "insert into app.deletion_certificates (case_id, counts, hashes, log_chain_heads)"
            " values (%s, %s, %s, %s) on conflict (case_id) do nothing",
            (
                ctx.case_id,
                Jsonb({"storage_objects": len(objects)}),
                Jsonb({"documents": [d["sha256"] for d in docs], "report_versions": [r["sha256"] for r in reports]}),
                Jsonb(heads["heads"] if heads else {}),
            ),
        )
    return True


async def delete_case(ctx: JobContext) -> None:
    """Deletes the case's files, key and rows, and records and sends the certificate. Resumable."""
    storage, crypto = services.get_storage(), services.get_crypto()
    if not await _start(ctx, storage):
        return

    async def remove_files(conn: AsyncConnection) -> None:
        # Everything under the case's prefix: documents, page images (quarantined and superseded
        # pages too), exhibits, exports and anything else a slice stored there.
        await storage.delete(await storage.list_recursive(case_prefix(ctx.case_id)))

    async def destroy_key(conn: AsyncConnection) -> None:
        await crypto.destroy(conn, ctx.case_id)

    await ctx.step("storage", remove_files)
    await ctx.step("key", destroy_key)

    # The rows go last, with the certificate in the same transaction: if anything fails, the purge
    # is undone and the job tries again (F20). Each email is recorded as sent in its own committed
    # step, so a retry never sends the certificate to the same recipient twice.
    async with ctx.transaction() as conn:
        recipients = await _all(conn, "select role, email from app.close_certificate_recipients(%s)", (ctx.case_id,))
        purged = await _one(conn, "select app.purge_case(%s) as counts", (ctx.case_id,))
        stamp = await _one(
            conn, "select now() as deleted_at, current_date + %s as backup_expiry_on", (BACKUP_RETENTION_DAYS,)
        )
        cert = await _one(
            conn, "select counts, sent_to from app.deletion_certificates where case_id = %s", (ctx.case_id,)
        )
        assert purged is not None and stamp is not None and cert is not None
        counts = {**cert["counts"], **purged["counts"]}
        sent = set(cert["sent_to"])
        for recipient in recipients:
            if recipient["role"] in sent:
                continue
            await ctx.mailer.send(
                deletion_certificate_mail(
                    recipient["email"],
                    case_id=ctx.case_id,
                    counts=counts,
                    deleted_at=stamp["deleted_at"],
                    backup_expiry_on=stamp["backup_expiry_on"],
                )
            )
            await _mark_sent(ctx, recipient["role"])
            sent.add(recipient["role"])
        await finish_certificate(conn, ctx.case_id, purged["counts"], stamp)


async def _mark_sent(ctx: JobContext, role: str) -> None:
    """Records, in its own committed transaction, that the certificate went to `role`."""
    async with ctx.transaction() as conn:
        await conn.execute(
            "update app.deletion_certificates set sent_to = array_append(sent_to, %s)"
            " where case_id = %s and deleted_at is null and not (%s = any(sent_to))",
            (role, ctx.case_id, role),
        )


async def finish_certificate(
    conn: AsyncConnection, case_id: uuid.UUID, counts: dict[str, Any], stamp: dict[str, Any]
) -> None:
    """Completes the certificate (after this it never changes)."""
    await conn.execute(
        "update app.deletion_certificates set counts = counts || %s, deleted_at = %s, backup_expiry_on = %s"
        " where case_id = %s",
        (Jsonb(counts), stamp["deleted_at"], stamp["backup_expiry_on"], case_id),
    )


# --- export_case ------------------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _finish_zip(archive: zipfile.ZipFile, names: list[str], hashes: list[dict[str, str]], case_id: uuid.UUID,
                variant: str) -> None:  # fmt: skip
    table = io.StringIO()
    writer = csv.DictWriter(table, fieldnames=["file", "recorded_sha256", "computed_sha256", "matches"])
    writer.writeheader()
    writer.writerows(hashes)
    manifest = {
        "case_id": str(case_id),
        "variant": variant,
        "created_at": datetime.now(UTC).isoformat(),
        "files": sorted(names),
        "not_yet_available": [{"path": path, "reason": reason} for path, reason in NOT_YET_AVAILABLE],
    }
    for path, _ in NOT_YET_AVAILABLE:
        archive.writestr(path, "not_yet_available\n")
    archive.writestr("hashes.csv", table.getvalue())
    archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    archive.close()


async def _deleting(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    """Locks the case against a deletion run starting (FOR SHARE) and says whether one has started."""
    case = await _one(conn, "select status from app.cases where id = %s for share", (case_id,))
    if case is None or case["status"] == "deleted":
        return True
    cert = await _one(conn, "select 1 as started from app.deletion_certificates where case_id = %s", (case_id,))
    return cert is not None


async def export_case(ctx: JobContext) -> None:
    """Builds both copies: `full` for the named expert (with the bundle) and `admin` for an
    organisation admin (report versions, hashes and logs; no bundle or page images).

    The case row stays share-locked until the copies are stored, so a deletion run starts either
    before (and this writes nothing) or after (and its Storage sweep finds the copies). Each copy is
    written to a temporary file one source file at a time; only the finished ZIP is read back to be
    encrypted (CaseCrypto seals a file whole)."""
    storage, crypto = services.get_storage(), services.get_crypto()
    async with ctx.transaction() as conn:
        if await _deleting(conn, ctx.case_id):
            return
        reports = await _all(
            conn,
            "select version_no, pdf_path, docx_path, sha256 from app.report_versions where case_id = %s"
            " and (pdf_path is not null or docx_path is not null) order by version_no",
            (ctx.case_id,),
        )
        documents = await _all(
            conn,
            "select id, storage_path, sha256 from app.documents where case_id = %s and storage_path is not null"
            " order by id",
            (ctx.case_id,),
        )
        with contextlib.ExitStack() as stack:
            files = {v: stack.enter_context(tempfile.TemporaryFile()) for v in VARIANTS}
            archives = {v: zipfile.ZipFile(files[v], "w", zipfile.ZIP_DEFLATED) for v in VARIANTS}
            names: dict[str, list[str]] = {v: [] for v in VARIANTS}
            hashes: dict[str, list[dict[str, str]]] = {v: [] for v in VARIANTS}

            def add(variants: tuple[str, ...], name: str, recorded: str | None, data: bytes) -> None:
                row = _hash_row(name, recorded, data)
                for v in variants:
                    archives[v].writestr(name, data)
                    names[v].append(name)
                    hashes[v].append(row)

            for r in reports:
                for path, ext, recorded in ((r["pdf_path"], "pdf", r["sha256"]), (r["docx_path"], "docx", None)):
                    if path is not None:
                        data = await crypto.decrypt(conn, ctx.case_id, await storage.get(path), purpose=path)
                        add(VARIANTS, f"report-versions/v{r['version_no']}.{ext}", recorded, data)
            for d in documents:
                path = d["storage_path"]
                data = await crypto.decrypt(conn, ctx.case_id, await storage.get(path), purpose=path)
                add(("full",), f"bundle/{d['id']}{PurePosixPath(path).suffix}", d["sha256"], data)
            for variant in VARIANTS:
                _finish_zip(archives[variant], names[variant], hashes[variant], ctx.case_id, variant)
                files[variant].seek(0)
                target = export_path(ctx.case_id, ctx.job_id, variant)
                blob = await crypto.encrypt(conn, ctx.case_id, files[variant].read(), purpose=target)
                await storage.put(target, blob)


def _hash_row(name: str, recorded: str | None, data: bytes) -> dict[str, str]:
    computed = _sha(data)
    return {
        "file": name,
        "recorded_sha256": recorded or "",
        "computed_sha256": computed,
        "matches": "yes" if recorded == computed else ("not_recorded" if recorded is None else "no"),
    }


# --- the daily close run ----------------------------------------------------------------------------


async def _as_case(conn: AsyncConnection, case_id: uuid.UUID | None) -> None:
    await conn.execute("select set_config('app.job_case_id', %s, true)", ("" if case_id is None else str(case_id),))


async def close_daily(reg: JobRegistry, conn: AsyncConnection, run_date: date) -> None:
    """Release ended holds, archive old closed cases, date closed cases, queue due deletions and
    remove export copies nobody downloaded in time."""
    await conn.execute("select app.close_release_expired_holds(%s)", (run_date,))
    await conn.execute("select app.close_auto_archive(%s, %s)", (run_date, ARCHIVE_AFTER_DAYS))
    library = services.get_packs()
    for row in await _all(conn, "select * from app.close_cases_without_retention(%s)", (run_date,)):
        try:
            async with conn.transaction():
                await _as_case(conn, row["case_id"])
                years = int(library.combine(row["jurisdiction"], row["case_type"], row["specialty"]).retention_years)
                policy = row["retention_policy"] or DEFAULT_POLICY
                case = await _one(
                    conn,
                    "select status, settled_at, app.close_withdrawn_on(id) as withdrawn_on from app.cases"
                    " where id = %s for update",
                    (row["case_id"],),
                )
                if case is not None:
                    closed = case["settled_at"].date() if case["settled_at"] is not None else case["withdrawn_on"]
                    base = closed or run_date
                    due = delete_after(policy, case["status"], base, run_date, years)
                    await conn.execute(
                        "update app.cases set retention_policy = %s, delete_after = %s where id = %s",
                        (policy, due, row["case_id"]),
                    )
        except Exception as exc:  # noqa: BLE001 - one case must not stop the run; it is retried tomorrow
            logger.error("close daily: dating a case failed: %s", type(exc).__name__)
        finally:
            await _as_case(conn, None)
    for row in await _all(conn, "select case_id from app.cases_due_for_deletion(%s)", (run_date,)):
        try:
            async with conn.transaction():
                await _as_case(conn, row["case_id"])
                queued = await _one(
                    conn,
                    "select 1 as queued from app.jobs where case_id = %s and kind = 'delete_case'"
                    " and status in ('queued', 'running')",
                    (row["case_id"],),
                )
                if queued is None:  # a held case is queued too: the job tells the expert it is paused
                    await reg.enqueue(conn, row["case_id"], "delete_case")
        except Exception as exc:  # noqa: BLE001 - as above
            logger.error("close daily: queueing a deletion failed: %s", type(exc).__name__)
        finally:
            await _as_case(conn, None)
    stale = await _all(
        conn,
        "select case_id, job_id from app.close_stale_exports(%s, %s, %s)",
        (run_date, EXPORT_KEEP_DAYS, EXPORT_SWEEP_WINDOW_DAYS),
    )
    for row in stale:
        try:
            await services.get_storage().delete([export_path(row["case_id"], row["job_id"], v) for v in VARIANTS])
        except Exception as exc:  # noqa: BLE001 - as above
            logger.error("close daily: removing an old export failed: %s", type(exc).__name__)


def register(reg: JobRegistry, *, daily_name: str = DAILY) -> None:
    reg.kind("export_case", changes_case=False)(export_case)
    reg.kind("delete_case", changes_case=True)(delete_case)

    async def daily(conn: AsyncConnection, run_date: date) -> None:
        await close_daily(reg, conn, run_date)

    reg.schedule(daily_name)(daily)


register(registry)
