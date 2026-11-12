"""Idempotency fingerprint and the body ceiling (audit 13, DP-13).

A request with an `Idempotency-Key` has its body read for the fingerprint before any route runs, so a route's own cap
(`read_capped`) saw a body that was already all in memory. The fingerprint now reads under a global ceiling
(`MAX_REQUEST_BODY_BYTES`): a declared length over it is refused before a byte is read, and a stream is cut off at it.
Small bodies get the same fingerprint as before and are still readable by the route.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from starlette.requests import Request

from app.core.config import document_limits
from app.core.errors import ApiError
from app.core.http import _fingerprint

pytestmark = pytest.mark.anyio

CEILING = 5000


class Wire:
    def __init__(self, body: bytes, chunk: int = 1000) -> None:
        self.body, self.chunk, self.sent = body, chunk, 0

    async def __call__(self) -> dict[str, Any]:
        piece = self.body[self.sent : self.sent + self.chunk]
        self.sent += len(piece)
        return {"type": "http.request", "body": piece, "more_body": self.sent < len(self.body)}


def request(wire: Wire, *, length: int | None = None, query: bytes = b"a=1") -> Request:
    headers = [(b"content-length", str(length).encode())] if length is not None else []
    return Request({"type": "http", "method": "PUT", "path": "/v1/x", "headers": headers, "query_string": query}, wire)


@pytest.fixture(autouse=True)
def ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(CEILING))


def test_the_ceiling_is_a_setting_defaulting_to_the_largest_route_cap() -> None:
    assert document_limits({}).max_request_body_bytes == 1024 * 1024 * 1024  # trial PDF, the largest route cap
    assert document_limits({"MAX_REQUEST_BODY_BYTES": "77"}).max_request_body_bytes == 77


async def test_a_declared_length_over_the_ceiling_is_refused_before_a_byte_is_read() -> None:
    wire = Wire(b"x" * 100_000)
    with pytest.raises(ApiError) as caught:
        await _fingerprint(request(wire, length=100_000))
    assert caught.value.status == 413
    assert wire.sent == 0


async def test_a_body_with_no_length_is_cut_off_at_the_ceiling() -> None:
    wire = Wire(b"x" * 50_000_000)
    with pytest.raises(ApiError) as caught:
        await _fingerprint(request(wire))
    assert caught.value.status == 413
    assert wire.sent <= CEILING + 1000, "stopped within one chunk of the ceiling"


async def test_a_lying_short_length_is_cut_off_at_the_ceiling() -> None:
    wire = Wire(b"x" * 50_000_000)
    with pytest.raises(ApiError):
        await _fingerprint(request(wire, length=10))
    assert wire.sent <= CEILING + 1000


async def test_a_small_body_has_the_same_fingerprint_as_before_and_the_route_can_still_read_it() -> None:
    body = b'{"tag": "one"}'
    req = request(Wire(body), length=len(body))
    expected = hashlib.sha256()
    for part in (b"PUT", b"/v1/x", b"a=1", body):
        expected.update(hashlib.sha256(part).digest())
    assert await _fingerprint(req) == expected.hexdigest()
    assert await req.body() == body
    assert b"".join([piece async for piece in req.stream()]) == body


async def test_an_empty_body_still_has_a_fingerprint() -> None:
    req = request(Wire(b""), length=0)
    assert len(await _fingerprint(req)) == 64
    assert await req.body() == b""
