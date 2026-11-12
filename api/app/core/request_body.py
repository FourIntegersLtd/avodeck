"""Read a whole request body without trusting its size (audit 13, DP-07).

`await request.body()` holds the entire body in memory before a route can look at it, and the app has no global
body cap. `read_capped` is the one way a route that takes the file as its body reads it: a declared
`Content-Length` over the cap is refused before a byte is read, and a stream (no length, or a length that lies) is
cut off as soon as it passes the cap. The chunk route in `core/routes/uploads.py` does the same for one part.

A route that is called with an `Idempotency-Key` has its body read for the request fingerprint first
(`core/http.py::_fingerprint`); `read_capped` then sees that cached body and only checks its length.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request

from app.core.errors import ApiError


def body_too_large() -> ApiError:
    return ApiError("body_too_large", "This is too large to send.", 413)


async def read_capped(request: Request, limit: int, *, too_large: Callable[[], Exception] = body_too_large) -> bytes:
    """The request body, or `too_large()` (default: 413 `body_too_large`) once it is known to pass `limit` bytes."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:  # refused before a byte of the body is read
        raise too_large()
    received = bytearray()
    async for piece in request.stream():  # cut off at the limit, whatever the sender claims
        received += piece
        if len(received) > limit:
            raise too_large()
    return bytes(received)
