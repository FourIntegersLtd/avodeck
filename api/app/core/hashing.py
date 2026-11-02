"""SHA-256 as hex text, for bytes in memory or a stream of pieces."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(pieces: Iterable[bytes]) -> str:
    """The hash of the pieces joined, without joining them."""
    digest = hashlib.sha256()
    for piece in pieces:
        digest.update(piece)
    return digest.hexdigest()
