"""Shared abstraction for reading a case's stored, encrypted files (architecture 5.3, rule 4).

`StoredDocument` yields decrypted chunks in order, raising specific errors when a part is missing or
corrupt. `spool()` writes the chunks into a seekable temporary file for consumers that need random access.

The temp file is `O_CREAT|O_EXCL`, mode `0600`, and **unlinked immediately after opening** so no path
survives a crash or restart (POSIX: the inode lives as long as the descriptor is open). A process-wide
semaphore (`AVODECK_SCRATCH_SLOTS`) bounds concurrent spools.

Residual risk: plaintext touches the container's ephemeral disk while a job runs; Railway's disk
encryption at rest is documented in the runbook.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import BinaryIO

import anyio
import anyio.to_thread
from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto, CiphertextInvalid
from app.core.storage import CaseFileStorage, StorageNotFound, upload_prefix

_log = logging.getLogger(__name__)
MAX_FILE_SIZE = 500 * 1024 * 1024  # the upload limit; the routes import it from here
DEFAULT_MAX_MEMORY = 32 * 1024 * 1024
LOW_SPACE_BYTES = 1024 * 1024 * 1024


class StoredFileMissing(Exception):
    """A chunk expected by the document is not in Storage (404)."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"missing: {path}")


class StoredFileCorrupt(Exception):
    """A chunk's ciphertext is invalid (wrong key, wrong case, tampered)."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"corrupt: {path}")


@dataclass(frozen=True)
class StoredDocument:
    """A document stored as encrypted chunks under a prefix in Supabase Storage."""

    storage: CaseFileStorage
    crypto: CaseCrypto
    case_id: uuid.UUID
    prefix: str
    paths: list[str]

    @classmethod
    def for_upload(
        cls, storage: CaseFileStorage, crypto: CaseCrypto, case_id: uuid.UUID, upload_id: uuid.UUID, chunk_count: int
    ) -> StoredDocument:
        """Build from an upload row: paths are `{prefix}/00001` … `{prefix}/{chunk_count:05d}`."""
        prefix = upload_prefix(case_id, upload_id)
        paths = [f"{prefix}/{n:05d}" for n in range(1, chunk_count + 1)]
        return cls(storage=storage, crypto=crypto, case_id=case_id, prefix=prefix, paths=paths)

    @classmethod
    async def for_prefix(
        cls, storage: CaseFileStorage, crypto: CaseCrypto, case_id: uuid.UUID, prefix: str
    ) -> StoredDocument:
        """Build from a Storage prefix, listing all objects (paginated)."""
        paths = sorted(await storage.list_recursive(prefix))
        return cls(storage=storage, crypto=crypto, case_id=case_id, prefix=prefix, paths=paths)

    async def chunks(self, conn: AsyncConnection) -> AsyncIterator[bytes]:
        """Yield decrypted chunks in order. Raises `StoredFileMissing` or `StoredFileCorrupt`."""
        for path in self.paths:
            try:
                blob = await self.storage.get(path)
            except StorageNotFound as exc:  # any other storage error propagates: the caller may retry
                raise StoredFileMissing(path) from exc
            try:
                yield await self.crypto.decrypt(conn, self.case_id, blob, purpose=path)
            except CiphertextInvalid as exc:
                raise StoredFileCorrupt(path) from exc

    @asynccontextmanager
    async def spool(
        self, conn: AsyncConnection, *, max_memory: int = DEFAULT_MAX_MEMORY, max_size: int = MAX_FILE_SIZE
    ) -> AsyncIterator[BinaryIO]:
        """Yield a seekable, readable binary file holding the whole decrypted document.

        A context manager rather than "bytes written": a seekable consumer (a PDF reader, an image
        library) wants the file itself, positioned at 0, and the guarantee that it is gone on exit.
        See `spool_chunks` for the guarantees.
        """
        async with spool_chunks(self.chunks(conn), max_memory=max_memory, max_size=max_size) as f:
            yield f


class SpoolTooLarge(Exception):
    """The content is larger than the cap (`MAX_FILE_SIZE` unless the caller sets one)."""


def _scratch_dir() -> str:
    """The private scratch directory (`AVODECK_SCRATCH_DIR`, else the system temp), created `0700` if missing."""
    directory = AVODECK_SCRATCH_DIR or tempfile.gettempdir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if AVODECK_SCRATCH_DIR:  # ours to lock down; never chmod the shared system temp directory
        os.chmod(directory, 0o700)
    return directory


def _free_bytes(directory: str) -> int:
    return shutil.disk_usage(directory).free


def warn_if_scratch_low() -> None:
    """Log a warning (a size only, never a path) when the scratch disk has under 1 GiB free."""
    free = _free_bytes(_scratch_dir())
    if free < LOW_SPACE_BYTES:
        _log.warning("scratch space is low", extra={"free_mib": free // (1024 * 1024)})


def _prepare_scratch() -> str:
    """Create the scratch directory and warn if its disk is low (every spool, one statvfs); its path."""
    directory = _scratch_dir()
    warn_if_scratch_low()
    return directory


def _roll_to_disk(buffer: io.BytesIO, directory: str) -> BinaryIO:
    """Move what is buffered into a new unlinked scratch file (in a thread: up to `max_memory` bytes)."""
    disk = _open_unlinked(directory)
    try:
        disk.write(buffer.getbuffer())
    except BaseException:
        disk.close()
        raise
    buffer.close()
    return disk


def _open_unlinked(directory: str) -> BinaryIO:
    """A new `0600` file in `directory`, created exclusively and unlinked before it is returned."""
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    while True:
        path = os.path.join(directory, f"spool-{uuid.uuid4().hex}")
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:  # pragma: no cover - a uuid collision
            continue
        break
    try:
        os.unlink(path)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "w+b")


@asynccontextmanager
async def spool_chunks(
    chunks: AsyncIterator[bytes], *, max_memory: int = DEFAULT_MAX_MEMORY, max_size: int = MAX_FILE_SIZE
) -> AsyncIterator[BinaryIO]:
    """Collect `chunks` into a seekable file, positioned at 0, closed on exit.

    Content up to `max_memory` bytes stays in memory; past that it moves to a scratch file that is
    `O_CREAT|O_EXCL`, mode `0600`, and unlinked as soon as it is open, so no path survives a crash.
    More than `max_size` bytes raises `SpoolTooLarge` before the caller is entered. One of
    `AVODECK_SCRATCH_SLOTS` process-wide slots is held for the whole block.
    """
    async with _slots:
        directory = await anyio.to_thread.run_sync(_prepare_scratch)  # fails fast; warns when space is low
        f: BinaryIO = io.BytesIO()
        total = 0
        try:
            async with aclosing(chunks) as source:  # type: ignore[type-var]  # async generators have aclose()
                async for chunk in source:
                    total += len(chunk)
                    if total > max_size:
                        raise SpoolTooLarge
                    if isinstance(f, io.BytesIO) and total > max_memory:
                        f = await anyio.to_thread.run_sync(_roll_to_disk, f, directory)
                    await anyio.to_thread.run_sync(f.write, chunk)
            await anyio.to_thread.run_sync(f.seek, 0)
            yield f
        finally:
            f.close()


# Environment knobs (read at import time; used by spool)
AVODECK_SCRATCH_DIR: str = os.environ.get("AVODECK_SCRATCH_DIR", "")


def scratch_slots(raw: str) -> int:
    """Concurrent spools allowed: `raw` (the environment value) or 2; below 1 would hang every spool, so refused."""
    value = int(raw) if raw.strip() else 2
    if value < 1:
        raise ValueError("AVODECK_SCRATCH_SLOTS must be at least 1")
    return value


AVODECK_SCRATCH_SLOTS: int = scratch_slots(os.environ.get("AVODECK_SCRATCH_SLOTS", ""))
_slots = anyio.Semaphore(AVODECK_SCRATCH_SLOTS)
