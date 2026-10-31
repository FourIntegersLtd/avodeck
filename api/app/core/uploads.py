"""The `finalize_upload` job: verify an upload's stored parts and register the document (architecture 5.3, F43).

`POST /v1/uploads/{id}/complete` only claims the upload (`open -> finalizing`) and queues this job, so a 500 MB
bundle is never read inside a request that a proxy could time out. The job streams the parts in order through
`StoredDocument`, one at a time and never the whole file in memory, checking each part against the SHA-256 kept
when it arrived, then the whole file against the one sent at the start, then the byte count. Outcomes:

- a part's object is gone: that part leaves `received` and `chunk_sha256` and the upload goes back to `open`, so
  the browser's next poll sees it missing and sends it again;
- a storage or network error: raised, so the queue retries with nothing changed; once the attempts are used up
  the job is `failed` and the next `complete` claims the upload again;
- a part that cannot be opened or does not match its kept hash: `failed` (`chunk_unreadable`), audited;
- the whole file's hash or the byte count is wrong: `failed` (`sha256_mismatch`, `size_mismatch`);
- all well: in one transaction the document, `finalizing -> complete`, the audit row and `DocumentUploaded`.

A failed upload's parts stay in Storage until the daily sweep removes them (`cleaned_at`), so they can be
inspected. Logs and audit rows carry ids, part numbers and counts, never content (rule 6).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.core import audit
from app.core.case_files import StoredDocument, StoredFileCorrupt, StoredFileMissing
from app.core.config import ConfigError
from app.core.contracts.events import DocumentUploaded, events
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext, registry
from app.core.storage import SupabaseStorage, storage_settings, upload_prefix

FINALIZE_UPLOAD = "finalize_upload"
_log = logging.getLogger(__name__)

# Two verifies at once at most per worker process: each streams a whole file through memory a part at a time.
_verifying = asyncio.Semaphore(2)

_files: tuple[SupabaseStorage, CaseCrypto] | None = None


def configure(files: tuple[SupabaseStorage, CaseCrypto] | None) -> None:
    """Use this storage and crypto from now on (None: build from the cell config on next use)."""
    global _files
    _files = files


def _services() -> tuple[SupabaseStorage, CaseCrypto]:
    global _files
    if _files is None:
        try:
            _files = (SupabaseStorage(storage_settings()), CaseCrypto(key_provider_from_env()))
        except ConfigError:
            raise ProcessingPaused("storage_unavailable") from None
    return _files


@dataclass(frozen=True)
class _Verdict:
    outcome: str  # verified | missing | unreadable | sha256_mismatch | size_mismatch
    part: int | None = None
    sha256: str | None = None


class _Superseded(Exception):
    """The upload moved on (expired, or another job claimed it) while this one was reading."""


def _part_number(path: str) -> int:
    return int(path.rsplit("/", 1)[1])


async def _verify(conn: Any, storage: SupabaseStorage, crypto: CaseCrypto, row: dict[str, Any]) -> _Verdict:
    document = StoredDocument.for_upload(storage, crypto, row["case_id"], row["id"], row["chunk_count"])
    kept: dict[str, str] = row["chunk_sha256"]
    digest = hashlib.sha256()
    total = number = 0
    try:
        async for data in document.chunks(conn):
            number += 1
            if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), kept.get(str(number), "")):
                return _Verdict("unreadable", number)
            digest.update(data)
            total += len(data)
    except StoredFileMissing as gone:
        return _Verdict("missing", _part_number(gone.path))
    except StoredFileCorrupt as bad:
        return _Verdict("unreadable", _part_number(bad.path))
    whole = digest.hexdigest()
    if not hmac.compare_digest(whole, str(row["sha256"])):
        return _Verdict("sha256_mismatch", sha256=whole)
    if total != int(row["size"]):
        return _Verdict("size_mismatch")
    return _Verdict("verified", sha256=whole)


async def finalize(ctx: JobContext, storage: SupabaseStorage, crypto: CaseCrypto) -> None:
    async with ctx.transaction() as conn:
        cur = await conn.execute(
            "select id, case_id, person_id, kind, filename, size, sha256, chunk_count, chunk_sha256, append"
            " from app.uploads where finalize_job_id = %s and status = 'finalizing'",
            (ctx.job_id,),
        )
        found = await cur.fetchone()
        if found is None:
            return  # another attempt already moved it on
        names = [c.name for c in cur.description or []]
        row = dict(zip(names, found, strict=True))
        async with _verifying:
            verdict = await _verify(conn, storage, crypto, row)
    ids = {"upload_id": str(row["id"]), "case_id": str(row["case_id"]), "job_id": str(ctx.job_id)}
    if verdict.outcome == "missing":
        await _reopen(ctx, row, verdict, ids)
    elif verdict.outcome == "verified":
        await _register(ctx, row, verdict, ids)
    else:
        await _fail(ctx, row, verdict, ids)


async def _reopen(ctx: JobContext, row: dict[str, Any], verdict: _Verdict, ids: dict[str, str]) -> None:
    async with ctx.transaction() as conn:
        cur = await conn.execute(
            "update app.uploads set status = 'open', received = array_remove(received, %s::int),"
            " chunk_sha256 = chunk_sha256 - %s::text"
            " where id = %s and status = 'finalizing' and finalize_job_id = %s returning id",
            (verdict.part, str(verdict.part), row["id"], ctx.job_id),
        )
        reopened = await cur.fetchone()
    if reopened is not None:
        _log.warning("upload.chunk_missing", extra={**ids, "part": verdict.part})


async def _fail(ctx: JobContext, row: dict[str, Any], verdict: _Verdict, ids: dict[str, str]) -> None:
    failure = "chunk_unreadable" if verdict.outcome == "unreadable" else verdict.outcome
    async with ctx.transaction() as conn:
        cur = await conn.execute(
            "update app.uploads set status = 'failed', failure = %s"
            " where id = %s and status = 'finalizing' and finalize_job_id = %s returning id",
            (failure, row["id"], ctx.job_id),
        )
        if await cur.fetchone() is None:
            return
        action = "upload.chunk_unreadable" if failure == "chunk_unreadable" else "upload.failed"
        await audit.record(conn, action=action, target=f"upload:{row['id']}", case_id=row["case_id"])
    _log.warning("upload.failed", extra={**ids, "failure": failure, "part": verdict.part})


async def _register(ctx: JobContext, row: dict[str, Any], verdict: _Verdict, ids: dict[str, str]) -> None:
    try:
        async with ctx.transaction() as conn:
            cur = await conn.execute(
                "insert into app.documents"
                " (case_id, kind, filename, storage_path, sha256, sha256_verified_at, uploaded_by)"
                " values (%s, %s, %s, %s, %s, now(), %s) returning id",
                (
                    row["case_id"],
                    row["kind"],
                    row["filename"],
                    upload_prefix(row["case_id"], row["id"]),
                    verdict.sha256,
                    row["person_id"],
                ),
            )
            created = await cur.fetchone()
            assert created is not None
            document_id: uuid.UUID = created[0]
            # The daily sweep does not take the upload's lock: if it expired the upload meanwhile, this matches
            # nothing and the document goes with the rolled-back transaction.
            cur = await conn.execute(
                "update app.uploads set status = 'complete', document_id = %s, completed_at = now()"
                " where id = %s and status = 'finalizing' and finalize_job_id = %s returning id",
                (document_id, row["id"], ctx.job_id),
            )
            if await cur.fetchone() is None:
                raise _Superseded
            await audit.record(
                conn, action="document.uploaded", target=f"document:{document_id}", case_id=row["case_id"]
            )
            await events.publish(
                conn, DocumentUploaded(case_id=row["case_id"], document_id=document_id, appended=bool(row["append"]))
            )
    except _Superseded:
        _log.info("upload.superseded", extra=ids)


@registry.kind(FINALIZE_UPLOAD, interactive=True, changes_case=False)
async def finalize_upload(ctx: JobContext) -> None:
    storage, crypto = _services()
    await finalize(ctx, storage, crypto)
