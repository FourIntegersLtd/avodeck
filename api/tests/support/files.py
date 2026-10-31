"""Shared pieces for upload, job and dictation tests: local Storage, a test app with them, synthetic files.

Files are made up from a seeded random generator, chunk by chunk, so a 300 MB upload never sits in
memory or in the repository. Nothing here is a real document.
"""

from __future__ import annotations

import hashlib
import logging
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.core.db import Database
    from app.core.storage import CaseFileStorage

MB = 1024 * 1024
CHUNK = 8 * MB
STORAGE_BUCKET = "case-files-test"
STORAGE_REGION = "eu-west-2"

_moto: Any = None


def moto_endpoint() -> str:
    """URL of a local S3 stand-in (moto), started once per test process on a free port, real HTTP."""
    global _moto
    if _moto is None:
        import atexit
        import socket

        from moto.server import ThreadedMotoServer

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # The stand-in's access log prints every key, which carries case ids: never in test output (rule 6).
        werkzeug = logging.getLogger("werkzeug")
        werkzeug.setLevel(logging.WARNING)
        werkzeug.propagate = False
        _moto = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
        _moto.start()
        atexit.register(_moto.stop)
    host, port = _moto.get_host_and_port()
    return f"http://{host}:{port}"


def age_objects(paths: list[str], *, days: int) -> None:
    """Make the stand-in report these objects as written `days` ago.

    S3 sets `LastModified` itself and there is no API to change it, so the test reaches into moto's
    own store, which runs in this process. The real adapter still reads the time over HTTP.
    """
    from moto.core import DEFAULT_ACCOUNT_ID  # type: ignore[attr-defined]
    from moto.s3.models import s3_backends

    bucket = s3_backends[DEFAULT_ACCOUNT_ID]["global"].buckets[STORAGE_BUCKET]
    when = datetime.now(UTC) - timedelta(days=days)
    for path in paths:
        bucket.keys[path].last_modified = when


def storage_env(auth_env: dict[str, str] | None = None, **overrides: str) -> dict[str, str]:
    """Settings for the adapter against the local S3 stand-in. `auth_env` is accepted so callers need not change."""
    return {
        **(auth_env or {}),
        "STORAGE_BUCKET": STORAGE_BUCKET,
        "AWS_REGION": STORAGE_REGION,
        "AWS_ALLOWED_REGIONS": STORAGE_REGION,
        "AWS_ACCESS_KEY_ID": "test-access-key",
        "AWS_SECRET_ACCESS_KEY": "test-secret-key",
        "S3_ENDPOINT_URL": moto_endpoint(),
        **overrides,
    }


async def local_storage(auth_env: dict[str, str] | None = None, **kwargs: Any) -> CaseFileStorage:
    from app.core.storage import CaseFileStorage, storage_settings

    storage = CaseFileStorage(storage_settings(storage_env(auth_env)), **kwargs)
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
