"""Shared pieces for upload, job and dictation tests: local Storage, a test app with them, synthetic files.

Files are made up from a seeded random generator, chunk by chunk, so a 300 MB upload never sits in
memory or in the repository. Nothing here is a real document.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.core.db import Database
    from app.core.storage import SupabaseStorage

MB = 1024 * 1024
CHUNK = 8 * MB
STORAGE_BUCKET = "case-files-test"


def storage_env(auth_env: dict[str, str]) -> dict[str, str]:
    return {**auth_env, "STORAGE_BUCKET": STORAGE_BUCKET}


async def local_storage(auth_env: dict[str, str]) -> SupabaseStorage:
    from app.core.storage import SupabaseStorage, storage_settings

    storage = SupabaseStorage(storage_settings(storage_env(auth_env)))
    await storage.ensure_bucket()
    return storage


def build_app(database: Database, auth_env: dict[str, str], **kwargs: Any) -> FastAPI:
    """The test app (as in conftest's `app`) plus whatever storage, speech or crypto a test passes."""
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(
        database=database, region="zz", auth=SupabaseAuth(auth_settings(auth_env)), mailer=OutboxMailer(), **kwargs
    )


class SyntheticFile:
    """A made-up file of `size` bytes, produced one chunk at a time."""

    def __init__(self, size: int, seed: int = 7, chunk: int = CHUNK) -> None:
        self.size = size
        self.seed = seed
        self.chunk_size = chunk
        self.count = -(-size // chunk)

    def chunk(self, n: int) -> bytes:
        """Chunk `n` (1-based)."""
        length = min(self.chunk_size, self.size - (n - 1) * self.chunk_size)
        return random.Random(self.seed * 100_003 + n).randbytes(length)

    def chunks(self) -> Iterator[tuple[int, bytes]]:
        for n in range(1, self.count + 1):
            yield n, self.chunk(n)

    def sha256(self) -> str:
        digest = hashlib.sha256()
        for _, data in self.chunks():
            digest.update(data)
        return digest.hexdigest()
