"""Chunked uploads and file downloads, through FastAPI only (rule 4; architecture 5.3, screen 04).

- POST /v1/cases/{case_id}/uploads starts an upload: 8 MB chunks, the whole file's SHA-256 up front.
- PUT /v1/uploads/{upload_id}/chunks/{n} stores chunk n encrypted under the case's key. A dropped
  chunk is sent again; the required `X-Chunk-SHA256` catches one damaged on the way.
- GET /v1/uploads/{upload_id} says which chunks are in and which are missing, to resume.
- POST /v1/uploads/{upload_id}/complete claims the upload and queues the `finalize_upload` job (`core/uploads.py`),
  which re-reads the stored parts, checks them against the whole file's SHA-256 sent in the body and registers
  the document: 202 while it works, 200 with the document once done. A request never reads the whole file.
- POST /v1/uploads/{upload_id}/cancel closes an open upload; its parts go with the daily sweep.
- GET /v1/cases/{case_id}/documents/{document_id}/file streams the file back, decrypted.

Storage paths and keys never leave the server. The job publishes `DocumentUploaded` in the transaction that makes
the document; slice A subscribes to queue scanning and processing.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import hmac
import logging
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Annotated

import psycopg
from fastapi import Depends, Request, Response
from fastapi.responses import StreamingResponse
from psycopg import AsyncConnection
from psycopg.errors import LockNotAvailable
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.core import audit
from app.core.auth.access import CaseAccess, check_case_access, require_case_access
from app.core.case_files import MAX_FILE_SIZE, StoredDocument, StoredFileMissing
from app.core.crypto import CaseCrypto, StoredFileChanged
from app.core.errors import CONFLICT_SQLSTATE, ApiError
from app.core.http import CurrentScope, DbConn, router
from app.core.jobs import registry
from app.core.storage import CaseFileStorage, upload_prefix
from app.core.uploads import FINALIZE_UPLOAD

CHUNK_SIZE = 8 * 1024 * 1024
CODE = r"^[a-z][a-z0-9_]{0,39}$"

LOCK_WAIT_MS = 5000  # the longest a call waits for another call on the same upload
SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
MAX_OPEN_UPLOADS = 20  # per person, open or finalizing

_log = logging.getLogger(__name__)
uploads_router = router(tags=["uploads"])


class UploadStart(BaseModel):
    kind: str = Field(pattern=CODE, description="A document kind from the case's packs.")
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0, description="Bytes. Zero is refused with `file_empty`.")
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


def _storage(request: Request) -> CaseFileStorage:
    storage: CaseFileStorage | None = request.app.state.storage
    if storage is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    return storage


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = request.app.state.crypto
    if crypto is None:
        raise ApiError("unavailable", "File storage is not ready.", 503)
    return crypto


Storage = Annotated[CaseFileStorage, Depends(_storage)]
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
        expires_at=expires_at.isoformat() if expires_at else None,  # type: ignore[attr-defined]
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
    if body.size == 0:
        raise ApiError("file_empty", "This file is empty. Check it and add it again.", 422)
    if CONTROL_CHARS.search(body.filename):
        raise ApiError("filename_invalid", "This file name has characters that cannot be used. Rename it.", 422)
    if body.size > MAX_FILE_SIZE:
        raise ApiError("file_too_large", "This file is too large. Split it into smaller files.", 422)
    counted = await conn.execute(
        "select count(*) from app.uploads where person_id = app.current_person_id()"
        " and status in ('open', 'finalizing') and expires_at > now()"
    )
    busy = await counted.fetchone()
    if busy is not None and busy[0] >= MAX_OPEN_UPLOADS:
        raise ApiError("too_many_uploads", "Too many files are being added at once. Wait for one to finish.", 429)
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
    # Ids, counts and sizes only: never the file name (rule 6).
    _log.info(
        "upload.started",
        extra={"upload_id": str(row["id"]), "case_id": str(case_id), "chunk_count": count, "size": body.size},
    )
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
        raise _closed()


def _too_large(expected: int) -> ApiError:
    return ApiError("chunk_too_large", "This part is larger than it should be. Send it again.", 413, expected=expected)


@uploads_router.put("/v1/uploads/{upload_id}/chunks/{n}", response_model=ChunkReceived)
async def put_chunk(
    upload_id: uuid.UUID, n: int, request: Request, conn: DbConn, storage: Storage, crypto: Crypto
) -> ChunkReceived:
    row = await _load(conn, upload_id)
    _open(row)
    count, size, chunk = int(row["chunk_count"]), int(row["size"]), int(row["chunk_size"])  # type: ignore[call-overload]
    if not 1 <= n <= count:
        raise ApiError("chunk_out_of_range", "This part is not in the file.", 422)
    expected = min(chunk, size - (n - 1) * chunk)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > expected:  # refused before a byte of the body is read
        raise _too_large(expected)
    received = bytearray()
    async for piece in request.stream():  # cut off at the expected size, whatever the sender claims
        received += piece
        if len(received) > expected:
            raise _too_large(expected)
    data = bytes(received)
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
    if n in (row["received"] or []):  # type: ignore[operator]
        _log.info("upload.chunk_resent", extra={"upload_id": str(upload_id), "case_id": str(case_id), "part": n})
    path = _chunk_path(_prefix(case_id, upload_id), n)
    await storage.put(path, await crypto.encrypt(conn, case_id, data, purpose=path))
    with _closed_when_refused():
        cur = await conn.execute(
            "update app.uploads set received = array(select distinct unnest(received || %s::int) order by 1),"
            " chunk_sha256 = chunk_sha256 || jsonb_build_object(%s::text, %s::text)"
            " where id = %s returning received",
            (n, str(n), part_sha, upload_id),
        )
        updated = await cur.fetchone()
    assert updated is not None
    return ChunkReceived(received=list(updated[0]))


class Finalizing(BaseModel):
    status: str = Field(description="`finalizing`: the file is being checked; poll the upload until it is `complete`.")


def _closed() -> ApiError:
    return ApiError("upload_closed", "This upload has finished. Start the upload again.", 409)


@contextlib.contextmanager
def _closed_when_refused() -> Iterator[None]:
    """The database guard refuses a change to a finished upload (`AV409`): that is a closed upload, not an error."""
    try:
        yield
    except psycopg.Error as exc:
        if exc.sqlstate == CONFLICT_SQLSTATE:
            raise _closed() from None
        raise


async def _job_is_live(conn: AsyncConnection, job_id: uuid.UUID | None) -> bool:
    if job_id is None:
        return False
    cur = await conn.execute("select status in ('queued', 'running') from app.jobs where id = %s", (job_id,))
    found = await cur.fetchone()
    return bool(found and found[0])


async def _audit_claim(conn: AsyncConnection, row: dict[str, object]) -> None:
    """Who asked for the file to be added, kept under the person (the job's own row has none)."""
    await audit.record(
        conn,
        action="upload.completed",
        target=f"upload:{row['id']}",
        case_id=row["case_id"],  # type: ignore[arg-type]
    )


async def _claim(
    conn: AsyncConnection, row: dict[str, object], claimed: str, response: Response
) -> UploadedDocument | Finalizing:
    """Branch on the upload's state (the upload's lock is held): the document, the job's state, or a new claim."""
    for _ in range(2):  # the second pass only if the sweep closed the upload between the read and the claim
        status = row["status"]
        if status == "complete":  # a repeat call: the same document, nothing re-read, if it is the same file
            if not hmac.compare_digest(str(row["sha256"]), claimed):
                raise ApiError("file_hash_mismatch", "This is not the file that was added. Add it again.", 409)
            response.status_code = 200
            return UploadedDocument(document_id=row["document_id"], sha256=row["sha256"], size=row["size"])  # type: ignore[arg-type]
        case_id: uuid.UUID = row["case_id"]  # type: ignore[assignment]
        if status == "finalizing":
            if await _job_is_live(conn, row["finalize_job_id"]):  # type: ignore[arg-type]
                return Finalizing(status="finalizing")
            if row["expired"]:  # its job is dead and the 7 days are over: not revived (the sweep closes it)
                raise _closed()
            # Its job failed or is gone: claim again with a fresh job.
            job = await registry.enqueue(conn, case_id, FINALIZE_UPLOAD)
            with _closed_when_refused():
                cur = await conn.execute(
                    "update app.uploads set finalize_job_id = %s, finalizing_at = now()"
                    " where id = %s and status = 'finalizing' and expires_at > now() returning id",
                    (job, row["id"]),
                )
            if await cur.fetchone() is not None:
                await _audit_claim(conn, row)
                return Finalizing(status="finalizing")
        elif status == "open" and not row["expired"]:
            upload = _upload(row)
            if upload.missing:
                raise ApiError("chunks_missing", "Some parts have not arrived yet.", 409, missing=upload.missing)
            if not hmac.compare_digest(str(row["sha256"]), claimed):
                raise ApiError("file_hash_mismatch", "The file did not verify after it was added. Add it again.", 422)
            job = await registry.enqueue(conn, case_id, FINALIZE_UPLOAD)
            with _closed_when_refused():
                cur = await conn.execute(
                    "update app.uploads set status = 'finalizing', finalize_job_id = %s, finalizing_at = now()"
                    " where id = %s and status = 'open' returning id",
                    (job, row["id"]),
                )
            if await cur.fetchone() is not None:
                await _audit_claim(conn, row)
                return Finalizing(status="finalizing")
        else:
            raise _closed()
        row = await _load(conn, row["id"])  # type: ignore[arg-type]
    raise _closed()


@uploads_router.post(
    "/v1/uploads/{upload_id}/complete",
    status_code=202,
    response_model=None,  # the two answers are declared below, one status each (no mixed union in the schema)
    responses={200: {"model": UploadedDocument}, 202: {"model": Finalizing}},
)
async def complete_upload(
    upload_id: uuid.UUID, response: Response, conn: DbConn, body: CompleteUpload | None = None
) -> UploadedDocument | Finalizing:
    """Claim the upload for finalisation: 202 while the worker checks the stored parts, 200 with the document
    once it has (a repeat call). The whole file's SHA-256 is required (422 `file_sha_missing`)."""
    await _load(conn, upload_id)  # not found / no access first, whatever else is wrong
    claimed = ((body.sha256 if body is not None else None) or "").lower()
    if not SHA256_HEX.fullmatch(claimed):
        raise ApiError("file_sha_missing", "The file's checksum was not sent. Add the file again.", 422)
    # Held until this transaction ends, so a second complete waits here and then sees the first one's claim.
    return await _claim(conn, await _load_locked(conn, upload_id), claimed, response)


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
    await audit.record(
        conn,
        action="upload.cancelled",
        target=f"upload:{upload_id}",
        case_id=row["case_id"],  # type: ignore[arg-type]
    )
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
    document = await StoredDocument.for_prefix(storage, crypto, case_id, row[0])
    cur = await conn.execute("select chunk_count from app.uploads where document_id = %s", (document_id,))
    made_from = await cur.fetchone()
    # A part gone from Storage is a 404 now, not a stream that breaks half way.
    if not document.paths or (made_from is not None and len(document.paths) != made_from[0]):
        raise ApiError("not_found", "Not found.", 404)
    database = request.app.state.database

    async def read(path: str, part_conn: AsyncConnection) -> bytes:
        one = dataclasses.replace(document, paths=[path])
        return b"".join([piece async for piece in one.chunks(part_conn)])

    try:
        first = await read(document.paths[0], conn)  # a part that is gone is a 404 now, not a broken stream later
    except StoredFileMissing:
        raise ApiError("not_found", "Not found.", 404) from None

    async def body() -> AsyncIterator[bytes]:
        # The request's transaction has closed by now: each later part is read in its own, as the same person.
        # The whole file must still match its SHA-256, or the stream stops with an error instead of
        # finishing as a file that is not the one uploaded.
        digest = hashlib.sha256(first)
        yield first
        for path in document.paths[1:]:
            async with database.request(scope) as part_conn:
                data = await read(path, part_conn)
            digest.update(data)
            yield data
        if digest.hexdigest() != expected:
            raise StoredFileChanged("stored file does not match its checksum")

    return StreamingResponse(
        body(), media_type="application/octet-stream", headers={"cache-control": "private, no-store"}
    )
