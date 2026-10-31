"""Chunked uploads and file downloads, through FastAPI only (rule 4; architecture 5.3, screen 04).

- POST /v1/cases/{case_id}/uploads starts an upload: 8 MB chunks, the whole file's SHA-256 up front.
- PUT /v1/uploads/{upload_id}/chunks/{n} stores chunk n encrypted under the case's key. A dropped
  chunk is sent again; the required `X-Chunk-SHA256` catches one damaged on the way.
- GET /v1/uploads/{upload_id} says which chunks are in and which are missing, to resume.
- POST /v1/uploads/{upload_id}/complete re-reads the stored parts, checks the whole file's SHA-256 sent
  in the body, then registers the document.
- GET /v1/cases/{case_id}/documents/{document_id}/file streams the file back, decrypted.

Storage paths and keys never leave the server. Completing publishes `DocumentUploaded` in the same transaction;
slice A subscribes to queue scanning and processing.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg import AsyncConnection
from psycopg.errors import LockNotAvailable
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.core import audit
from app.core.auth.access import CaseAccess, check_case_access, require_case_access
from app.core.contracts.events import DocumentUploaded, events
from app.core.crypto import CaseCrypto, StoredFileChanged
from app.core.db import Database, RequestScope
from app.core.errors import ApiError, error_response
from app.core.http import CurrentScope, DbConn, router
from app.core.storage import StorageError, SupabaseStorage, upload_prefix

CHUNK_SIZE = 8 * 1024 * 1024
MAX_FILE_SIZE = 500 * 1024 * 1024
CODE = r"^[a-z][a-z0-9_]{0,39}$"

LOCK_WAIT_MS = 5000  # the longest a call waits for another call on the same upload
SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")
_log = logging.getLogger(__name__)

uploads_router = router(tags=["uploads"])


class UploadStart(BaseModel):
    kind: str = Field(pattern=CODE, description="A document kind from the case's packs.")
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    append: bool | None = Field(default=None, description="Later records added to a case that already has a bundle.")


class Upload(BaseModel):
    upload_id: uuid.UUID
    case_id: uuid.UUID
    status: str
    size: int
    chunk_size: int
    chunk_count: int
    received: list[int]
    missing: list[int]
    document_id: uuid.UUID | None
    failure: str | None = Field(default=None, description="Why the upload failed (code only).")
    expires_at: str | None = Field(default=None, description="ISO 8601 expiry time.")
    sha256: str | None = Field(default=None, description="The whole file's SHA-256 sent at upload start.")
    filename: str | None = Field(default=None, description="Original filename.")
    kind: str | None = Field(default=None, description="Document kind from the case's packs.")


class CompleteUpload(BaseModel):
    sha256: str | None = Field(
        default=None, description="The whole file's SHA-256 (64 hex characters), worked out by the sender."
    )


class ChunkReceived(BaseModel):
    received: list[int]


class UploadedDocument(BaseModel):
    document_id: uuid.UUID
    sha256: str
    size: int


def _storage(request: Request) -> SupabaseStorage:
    storage: SupabaseStorage | None = request.app.state.storage
    if storage is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    return storage


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = request.app.state.crypto
    if crypto is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    return crypto


Storage = Annotated[SupabaseStorage, Depends(_storage)]
Crypto = Annotated[CaseCrypto, Depends(_crypto)]


def _prefix(case_id: uuid.UUID, upload_id: uuid.UUID) -> str:
    return upload_prefix(case_id, upload_id)


def _chunk_path(prefix: str, n: int) -> str:
    return f"{prefix}/{n:05d}"


def _upload(row: dict[str, object]) -> Upload:
    received: list[int] = sorted(row["received"])  # type: ignore[call-overload]
    count = int(row["chunk_count"])  # type: ignore[call-overload]
    expires_at = row.get("expires_at")
    return Upload(
        upload_id=row["id"],  # type: ignore[arg-type]
        case_id=row["case_id"],  # type: ignore[arg-type]
        status=row["status"],  # type: ignore[arg-type]
        size=row["size"],  # type: ignore[arg-type]
        chunk_size=row["chunk_size"],  # type: ignore[arg-type]
        chunk_count=count,
        received=received,
        missing=sorted(set(range(1, count + 1)) - set(received)),
        document_id=row["document_id"],  # type: ignore[arg-type]
        failure=row.get("failure"),  # type: ignore[arg-type]
        expires_at=expires_at.isoformat() if expires_at else None,  # type: ignore[union-attr]
        sha256=row.get("sha256"),  # type: ignore[arg-type]
        filename=row.get("filename"),  # type: ignore[arg-type]
        kind=row.get("kind"),  # type: ignore[arg-type]
    )


async def _load(conn: AsyncConnection, upload_id: uuid.UUID) -> dict[str, object]:
    """The upload, if the person may upload to its case; 404 otherwise (as for any case resource)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, case_id, status, size, sha256, chunk_size, chunk_count, received, chunk_sha256, document_id,"
            " kind, filename, append, expires_at, failure, finalize_job_id,"
            " expires_at < now() as expired from app.uploads where id = %s",
            (upload_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise ApiError("not_found", "Not found.", 404)
    await check_case_access(conn, row["case_id"], "case.upload")
    return row


@uploads_router.post("/v1/cases/{case_id}/uploads", status_code=201, response_model=Upload)
async def start_upload(
    case_id: uuid.UUID,
    body: UploadStart,
    access: Annotated[CaseAccess, Depends(require_case_access("case.upload"))],
    conn: DbConn,
) -> Upload:
    if body.size > MAX_FILE_SIZE:
        raise ApiError("file_too_large", "This file is too large. Split it into smaller files.", 422)
    count = -(-body.size // CHUNK_SIZE)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "insert into app.uploads"
            " (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count, append)"
            " values (%s, app.current_person_id(), %s, %s, %s, %s, %s, %s, %s)"
            " returning id, case_id, status, size, chunk_size, chunk_count, received, document_id,"
            " sha256, filename, kind, expires_at, failure",
            (case_id, body.kind, body.filename, body.size, body.sha256, CHUNK_SIZE, count, bool(body.append)),
        )
        row = await cur.fetchone()
    assert row is not None
    return _upload(row)


@uploads_router.get("/v1/uploads/{upload_id}", response_model=Upload)
async def get_upload(upload_id: uuid.UUID, conn: DbConn) -> Upload:
    return _upload(await _load(conn, upload_id))


async def _load_locked(conn: AsyncConnection, upload_id: uuid.UUID) -> dict[str, object]:
    """The upload, read again once this transaction holds the upload's lock (held until it ends).

    An advisory lock, not `for update` on the row: a failed check closes the upload from a second
    transaction (`_fail`), which would wait on a row lock forever. Receiving a part and completing both take
    it, so a part cannot land between completing's read of the parts and the document being made."""
    await _load(conn, upload_id)  # not found / no access, before anything is locked
    # Wait a few seconds at most for another call on the same upload, then say so; later statements wait as usual.
    await conn.execute("select set_config('lock_timeout', %s, true)", (f"{LOCK_WAIT_MS}ms",))
    try:
        await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s::text, 0))", (str(upload_id),))
    except LockNotAvailable:
        raise ApiError("upload_busy", "This upload is being worked on. Try again in a moment.", 409) from None
    await conn.execute("select set_config('lock_timeout', '0', true)")
    return await _load(conn, upload_id)


def _open(row: dict[str, object]) -> None:
    if row["status"] != "open" or row["expired"]:
        raise ApiError("upload_closed", "This upload has finished. Start the upload again.", 409)


@uploads_router.put("/v1/uploads/{upload_id}/chunks/{n}", response_model=ChunkReceived)
async def put_chunk(
    upload_id: uuid.UUID, n: int, request: Request, conn: DbConn, storage: Storage, crypto: Crypto
) -> ChunkReceived:
    row = await _load(conn, upload_id)
    _open(row)
    count, size, chunk = int(row["chunk_count"]), int(row["size"]), int(row["chunk_size"])  # type: ignore[call-overload]
    if not 1 <= n <= count:
        raise ApiError("chunk_out_of_range", "This part is not in the file.", 422)
    data = await request.body()
    expected = min(chunk, size - (n - 1) * chunk)
    if len(data) != expected:
        raise ApiError("chunk_size_wrong", "This part did not arrive whole. Send it again.", 422, expected=expected)
    sent_sha = request.headers.get("x-chunk-sha256", "")
    if not SHA256_HEX.fullmatch(sent_sha):
        raise ApiError("chunk_sha_missing", "This part was sent without its checksum. Send it again.", 422)
    # The hash of the bytes as received, before they are encrypted; compared in constant time.
    part_sha = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(part_sha, sent_sha.lower()):
        raise ApiError("chunk_corrupted", "This part was damaged on the way. Send it again.", 422)
    # Lock, then look again: completing may have finished (or failed) the upload while this part was read.
    _open(await _load_locked(conn, upload_id))
    case_id: uuid.UUID = row["case_id"]  # type: ignore[assignment]
    path = _chunk_path(_prefix(case_id, upload_id), n)
    await storage.put(path, await crypto.encrypt(conn, case_id, data, purpose=path))
    cur = await conn.execute(
        "update app.uploads set received = array(select distinct unnest(received || %s::int) order by 1),"
        " chunk_sha256 = chunk_sha256 || jsonb_build_object(%s::text, %s::text)"
        " where id = %s returning received",
        (n, str(n), part_sha, upload_id),
    )
    updated = await cur.fetchone()
    assert updated is not None
    return ChunkReceived(received=list(updated[0]))


async def _close_failed(
    database: Database, scope: RequestScope, upload: Upload, storage: SupabaseStorage, why: dict[str, object]
) -> None:
    """Close an upload whose stored bytes do not match, for good, and delete its stored parts, but only if
    this call is the one that closed it: an upload another call has already finished keeps its parts.
    Logs ids and hashes (or a part number), never any content. Runs after the error response has gone and
    the request's transaction (with its lock and connection) is over, in a transaction of its own."""
    try:
        async with database.request(scope) as conn:
            cur = await conn.execute(
                "update app.uploads set status = 'failed' where id = %s and status = 'open' returning id",
                (upload.upload_id,),
            )
            closed = await cur.fetchone()
            if closed is not None:
                await audit.record(
                    conn, action="upload.failed", target=f"upload:{upload.upload_id}", case_id=upload.case_id
                )
        if closed is None:
            return
        _log.warning(
            "upload failed its stored-bytes check",
            extra={"upload_id": str(upload.upload_id), "case_id": str(upload.case_id), **why},
        )
        prefix = _prefix(upload.case_id, upload.upload_id)
        await storage.delete([_chunk_path(prefix, n) for n in range(1, upload.chunk_count + 1)])
    except Exception as exc:  # noqa: BLE001 - nothing to tell the sender; the upload stays open until it expires
        _log.error("failed upload not closed", extra={"upload_id": str(upload.upload_id), "error": type(exc).__name__})


def _failed(
    request: Request, upload: Upload, storage: SupabaseStorage, why: dict[str, object], error: ApiError
) -> JSONResponse:
    """The error response for a failed check, carrying the work of closing the upload to run once it is sent.

    Returned, not raised, and closed afterwards, so the failing call never holds a second pooled connection
    while it still holds its first (and the upload's lock): ten failing calls at once cannot starve the pool."""
    response = error_response(error.status, error.code, error.message, **error.extra)
    response.background = BackgroundTask(
        _close_failed, request.app.state.database, request.state.scope, upload, storage, why
    )
    return response


@uploads_router.post("/v1/uploads/{upload_id}/complete", status_code=201, response_model=UploadedDocument)
async def complete_upload(
    upload_id: uuid.UUID,
    response: Response,
    request: Request,
    conn: DbConn,
    storage: Storage,
    crypto: Crypto,
    body: CompleteUpload | None = None,
) -> UploadedDocument | JSONResponse:
    await _load(conn, upload_id)  # not found / no access first, whatever else is wrong
    claimed = ((body.sha256 if body is not None else None) or "").lower()
    if not SHA256_HEX.fullmatch(claimed):
        raise ApiError("file_sha_missing", "The file's checksum was not sent. Add the file again.", 422)
    # Held until this transaction ends, so the parts cannot change between being read and the document made,
    # and a second complete waits here and then sees the first one's result.
    row = await _load_locked(conn, upload_id)
    if row["status"] == "complete":  # a repeat call: the same document, nothing re-read, if it is the same file
        if not hmac.compare_digest(str(row["sha256"]), claimed):
            raise ApiError("file_hash_mismatch", "This is not the file that was added. Add it again.", 409)
        response.status_code = 200
        return UploadedDocument(document_id=row["document_id"], sha256=row["sha256"], size=row["size"])  # type: ignore[arg-type]
    _open(row)
    upload = _upload(row)
    if upload.missing:
        raise ApiError("chunks_missing", "Some parts have not arrived yet.", 409, missing=upload.missing)
    case_id = upload.case_id
    prefix = _prefix(case_id, upload_id)
    # Read back the stored parts in order, one at a time, into one running hash: never the whole file at once.
    digest = hashlib.sha256()
    part_hashes: dict[str, str] = row["chunk_sha256"]  # type: ignore[assignment]
    for n in range(1, upload.chunk_count + 1):
        path = _chunk_path(prefix, n)
        try:
            stored = await storage.get(path)
        except StorageError:
            # A failed check on a call just ahead of this one may be deleting the parts as we read: if the
            # part is gone the upload is closed; if it is still there, storage itself failed.
            if path in await storage.list(prefix):
                raise
            raise ApiError("upload_closed", "This upload has finished. Start the upload again.", 409) from None
        data = await crypto.decrypt(conn, case_id, stored, purpose=path)
        # Each part must still be the bytes whose hash was kept when it arrived.
        if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), part_hashes.get(str(n), "")):
            error = ApiError("chunk_hash_mismatch", "A part changed after it arrived. Add the file again.", 422, part=n)
            return _failed(request, upload, storage, {"part": n}, error)
        digest.update(data)
    stored_sha = digest.hexdigest()
    if not hmac.compare_digest(stored_sha, claimed):
        error = ApiError("file_hash_mismatch", "The file did not verify after it was added. Add it again.", 422)
        return _failed(request, upload, storage, {"expected_sha256": claimed, "stored_sha256": stored_sha}, error)
    if not hmac.compare_digest(stored_sha, str(row["sha256"])):
        error = ApiError("sha256_mismatch", "The file changed on the way. Upload it again.", 422)
        why: dict[str, object] = {"expected_sha256": str(row["sha256"]), "stored_sha256": stored_sha}
        return _failed(request, upload, storage, why, error)
    cur = await conn.execute(
        "insert into app.documents (case_id, kind, filename, storage_path, sha256, sha256_verified_at, uploaded_by)"
        " values (%s, %s, %s, %s, %s, now(), app.current_person_id()) returning id",
        (case_id, row["kind"], row["filename"], prefix, stored_sha),
    )
    created = await cur.fetchone()
    assert created is not None
    document_id: uuid.UUID = created[0]
    # The daily sweep does not take the upload's lock: if it expired the upload since the check above, this
    # matches nothing, and the document made a moment ago goes with the rolled-back transaction.
    cur = await conn.execute(
        "update app.uploads set status = 'complete', document_id = %s, completed_at = now()"
        " where id = %s and status = 'open' returning id",
        (document_id, upload_id),
    )
    if await cur.fetchone() is None:
        raise ApiError("upload_closed", "This upload has finished. Start the upload again.", 409)
    await audit.record(conn, action="document.uploaded", target=f"document:{document_id}", case_id=case_id)
    await events.publish(conn, DocumentUploaded(case_id=case_id, document_id=document_id, appended=bool(row["append"])))
    return UploadedDocument(document_id=document_id, sha256=stored_sha, size=upload.size)


@uploads_router.post("/v1/uploads/{upload_id}/cancel", response_model=Upload)
async def cancel_upload(upload_id: uuid.UUID, conn: DbConn) -> Upload:
    """Cancel an open upload. Its stored parts are left to the daily sweep (`cleaned_at` null)."""
    row = await _load(conn, upload_id)
    if row["status"] != "open":
        raise ApiError("upload_closed", "This upload has already finished.", 409)
    cur = await conn.execute(
        "update app.uploads set status = 'cancelled' where id = %s and status = 'open' returning id",
        (upload_id,),
    )
    if await cur.fetchone() is None:
        raise ApiError("upload_closed", "This upload has already finished.", 409)
    await audit.record(conn, action="upload.cancelled", target=f"upload:{upload_id}", case_id=row["case_id"])
    return _upload({**row, "status": "cancelled"})


@uploads_router.get("/v1/cases/{case_id}/documents/{document_id}/file", response_class=StreamingResponse)
async def download_document(
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.view_content"))],
    request: Request,
    conn: DbConn,
    scope: CurrentScope,
    storage: Storage,
    crypto: Crypto,
) -> StreamingResponse:
    cur = await conn.execute(
        "select storage_path, sha256 from app.documents where id = %s and case_id = %s", (document_id, case_id)
    )
    row = await cur.fetchone()
    if row is None or not row[0]:
        raise ApiError("not_found", "Not found.", 404)
    expected: str = row[1]
    paths = sorted(await storage.list(row[0]))
    if not paths:
        raise ApiError("not_found", "Not found.", 404)
    database = request.app.state.database

    async def body() -> AsyncIterator[bytes]:
        # The request's transaction has closed by now: each part is read in its own, as the same person.
        # Each part opens only at its own path; the whole file must still match its SHA-256, or the
        # stream stops with an error instead of finishing as a file that is not the one uploaded.
        digest = hashlib.sha256()
        for path in paths:
            blob = await storage.get(path)
            async with database.request(scope) as part_conn:
                data = await crypto.decrypt(part_conn, case_id, blob, purpose=path)
            digest.update(data)
            yield data
        if digest.hexdigest() != expected:
            raise StoredFileChanged("stored file does not match its checksum")

    return StreamingResponse(
        body(), media_type="application/octet-stream", headers={"cache-control": "private, no-store"}
    )
