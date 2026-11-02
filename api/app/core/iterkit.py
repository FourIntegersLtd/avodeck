"""Small iteration helpers."""

from __future__ import annotations

from collections.abc import Iterator


def iter_chunks(data: bytes, size: int) -> Iterator[bytes]:
    """`data` in pieces of `size` bytes (the last may be shorter), without copying it all at once."""
    if size <= 0:
        raise ValueError("size must be positive")
    view = memoryview(data)
    for start in range(0, len(view), size):
        yield bytes(view[start : start + size])
