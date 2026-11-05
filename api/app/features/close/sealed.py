"""Export copies sealed frame by frame (stub: filled in by the next commit)."""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto

MAGIC = b"AVXZ\x01"
FRAME_SIZE = 4 * 1024 * 1024


async def open_sealed(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, blob: bytes, *, purpose: str
) -> bytes:
    return await crypto.decrypt(conn, case_id, blob, purpose=purpose)
