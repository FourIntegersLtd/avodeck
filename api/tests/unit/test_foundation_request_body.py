"""`read_capped` (audit 13, DP-07): a request body is refused as soon as it passes the cap, not after it is all read."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from app.core.errors import ApiError
from app.core.request_body import read_capped

pytestmark = pytest.mark.anyio


class Wire:
    """An ASGI `receive` that hands out `total` bytes in chunks and counts what was actually asked for."""

    def __init__(self, total: int, chunk: int = 1000) -> None:
        self.total, self.chunk, self.sent = total, chunk, 0

    async def __call__(self) -> dict[str, Any]:
        n = min(self.chunk, self.total - self.sent)
        self.sent += n
        return {"type": "http.request", "body": b"x" * n, "more_body": self.sent < self.total}


def request(wire: Wire, *, length: int | None = None) -> Request:
    headers = [(b"content-length", str(length).encode())] if length is not None else []
    return Request({"type": "http", "method": "PUT", "path": "/", "headers": headers, "query_string": b""}, wire)


async def test_a_body_within_the_cap_is_returned_whole() -> None:
    wire = Wire(2500)
    assert await read_capped(request(wire, length=2500), 5000) == b"x" * 2500


async def test_a_body_exactly_at_the_cap_is_allowed() -> None:
    assert len(await read_capped(request(Wire(3000), length=3000), 3000)) == 3000


async def test_a_declared_length_over_the_cap_is_refused_before_a_byte_is_read() -> None:
    wire = Wire(10_000)
    with pytest.raises(ApiError) as caught:
        await read_capped(request(wire, length=10_000), 5000)
    assert caught.value.status_code == 413
    assert wire.sent == 0


async def test_a_stream_with_no_length_is_cut_off_at_the_cap() -> None:
    wire = Wire(10_000_000)
    with pytest.raises(ApiError) as caught:
        await read_capped(request(wire), 5000)
    assert caught.value.status_code == 413
    assert wire.sent <= 6000, "stopped within one chunk of the cap"


async def test_a_lying_short_length_does_not_get_past_the_cap() -> None:
    wire = Wire(10_000_000)
    with pytest.raises(ApiError):
        await read_capped(request(wire, length=10), 5000)
    assert wire.sent <= 6000


async def test_the_caller_chooses_the_error() -> None:
    with pytest.raises(KeyError):
        await read_capped(request(Wire(9000)), 5000, too_large=lambda: KeyError("mine"))


async def test_a_negative_or_text_length_is_ignored_and_the_stream_is_still_capped() -> None:
    wire = Wire(10_000_000)
    req = Request(
        {"type": "http", "method": "PUT", "path": "/", "headers": [(b"content-length", b"abc")], "query_string": b""},
        wire,
    )
    with pytest.raises(ApiError):
        await read_capped(req, 5000)
