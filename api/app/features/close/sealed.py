"""Export copies sealed frame by frame, so the whole archive is never held in memory (scalability PR 13).

`CaseCrypto` seals a file whole (one AES-GCM seal). An export ZIP can be as large as the bundle, so it is
sealed in frames of `FRAME_SIZE` read from the temporary file, each frame under the case key (the same
envelope scheme as every other stored file), and written to a second temporary file.

Stored layout (one object, `exports/<job>/<variant>.zip`):
    MAGIC, then for each frame: 4-byte big-endian length, then the sealed frame.
A frame's purpose is `<path>#<index>`, and the last frame's is `<path>#<index>#end`, so a frame that is
moved, dropped or cut off from the end fails to open (CiphertextInvalid). A copy written before this
(one sealed blob, starting with the ciphertext version byte) still opens.
"""

from __future__ import annotations

import uuid
from typing import BinaryIO

import anyio.to_thread
from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto, CiphertextInvalid

MAGIC = b"AVXZ\x01"
FRAME_SIZE = 4 * 1024 * 1024
_LENGTH_BYTES = 4


def _purpose(path: str, index: int, *, last: bool) -> str:
    return f"{path}#{index}" + ("#end" if last else "")


async def seal_file(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, source: BinaryIO, target: BinaryIO, *, purpose: str
) -> None:
    """Seals `source` (from its current position) into `target`, one frame in memory at a time."""
    frame = FRAME_SIZE
    await anyio.to_thread.run_sync(target.write, MAGIC)
    current = await anyio.to_thread.run_sync(source.read, frame)
    index = 0
    while True:
        following = await anyio.to_thread.run_sync(source.read, frame) if current else b""
        last = not following
        sealed = await crypto.encrypt(conn, case_id, current, purpose=_purpose(purpose, index, last=last))
        await anyio.to_thread.run_sync(target.write, len(sealed).to_bytes(_LENGTH_BYTES, "big") + sealed)
        if last:
            return
        current, index = following, index + 1


async def open_sealed(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, blob: bytes, *, purpose: str
) -> bytes:
    """The plain bytes of a stored export copy, framed or from before framing."""
    if not blob.startswith(MAGIC):
        return await crypto.decrypt(conn, case_id, blob, purpose=purpose)
    view = memoryview(blob)
    at, index, parts = len(MAGIC), 0, []
    while True:
        if at + _LENGTH_BYTES > len(view):
            raise CiphertextInvalid("the sealed copy is cut off")
        size = int.from_bytes(view[at : at + _LENGTH_BYTES], "big")
        start = at + _LENGTH_BYTES
        if start + size > len(view):
            raise CiphertextInvalid("the sealed copy is cut off")
        last = start + size == len(view)
        frame = bytes(view[start : start + size])
        parts.append(await crypto.decrypt(conn, case_id, frame, purpose=_purpose(purpose, index, last=last)))
        if last:
            return b"".join(parts)
        at, index = start + size, index + 1
