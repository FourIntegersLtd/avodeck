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

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto, CiphertextInvalid
from app.core.storage import StorageNotFound, SupabaseStorage, upload_prefix


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

    storage: SupabaseStorage
    crypto: CaseCrypto
    case_id: uuid.UUID
    prefix: str
    paths: list[str]

    @classmethod
    def for_upload(
        cls, storage: SupabaseStorage, crypto: CaseCrypto, case_id: uuid.UUID, upload_id: uuid.UUID, chunk_count: int
    ) -> StoredDocument:
        """Build from an upload row: paths are `{prefix}/00001` … `{prefix}/{chunk_count:05d}`."""
        prefix = upload_prefix(case_id, upload_id)
        paths = [f"{prefix}/{n:05d}" for n in range(1, chunk_count + 1)]
        return cls(storage=storage, crypto=crypto, case_id=case_id, prefix=prefix, paths=paths)

    @classmethod
    async def for_prefix(
        cls, storage: SupabaseStorage, crypto: CaseCrypto, case_id: uuid.UUID, prefix: str
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

    async def spool(self, conn: AsyncConnection, *, max_size: int = 500 * 1024 * 1024) -> int:
        """Write decrypted content to a temporary file; returns bytes written.

        TODO: implement with unlinked temp file, semaphore, and size cap. Stub raises NotImplementedError.
        """
        raise NotImplementedError("spool() is not yet implemented")


# Environment knobs (read at import time for the startup warning; used by spool)
AVODECK_SCRATCH_DIR: str = os.environ.get("AVODECK_SCRATCH_DIR", "")
AVODECK_SCRATCH_SLOTS: int = int(os.environ.get("AVODECK_SCRATCH_SLOTS", "2"))
