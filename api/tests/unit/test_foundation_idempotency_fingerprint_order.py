"""Idempotency fingerprint: small ceiling, and not before the session check (audit 13 reviewer round, F-c).

`_fingerprint` used to buffer up to the 1 GiB global ceiling before the session was checked, so a request with any
cookie and an Idempotency-Key could make the API hold a large body. Now a body is read for the fingerprint only up
to a small ceiling (`MAX_FINGERPRINT_BODY_BYTES`); a declared length above it is fingerprinted from its length and
type without reading it (the route's own cap then applies), a stream that passes it is refused, and the fingerprint
is made only once the session is known.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.core.config import document_limits
from app.core.errors import ApiError
from app.core.http import _fingerprint
from app.core.request_body import read_capped
from tests.support.auth import APP_ORIGIN, CSRF_HEADER

pytestmark = pytest.mark.anyio

SMALL = 5000
GLOBAL = 1_000_000


class Wire:
    def __init__(self, body: bytes, chunk: int = 1000) -> None:
        self.body, self.chunk, self.sent = body, chunk, 0

    async def __call__(self) -> dict[str, Any]:
        piece = self.body[self.sent : self.sent + self.chunk]
        self.sent += len(piece)
        return {"type": "http.request", "body": piece, "more_body": self.sent < len(self.body)}


def request(wire: Wire, *, length: int | None = None, kind: bytes = b"application/pdf") -> Request:
    headers = [(b"content-type", kind)]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return Request({"type": "http", "method": "PUT", "path": "/v1/x", "headers": headers, "query_string": b""}, wire)


@pytest.fixture(autouse=True)
def ceilings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FINGERPRINT_BODY_BYTES", str(SMALL))
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(GLOBAL))


def test_the_fingerprint_ceiling_is_a_small_setting() -> None:
    assert document_limits({}).max_fingerprint_body_bytes == 16 * 1024 * 1024
    assert document_limits({"MAX_FINGERPRINT_BODY_BYTES": "77"}).max_fingerprint_body_bytes == 77


async def test_a_declared_length_over_the_small_ceiling_is_fingerprinted_without_reading_the_body() -> None:
    wire = Wire(b"x" * 100_000)
    req = request(wire, length=100_000)
    first = await _fingerprint(req)
    assert len(first) == 64
    assert wire.sent == 0
    other = request(Wire(b"x" * 100_001), length=100_001)
    assert await _fingerprint(other) != first  # the length is part of what is compared


async def test_the_route_can_still_read_a_body_that_was_not_buffered_for_the_fingerprint() -> None:
    body = b"x" * 100_000
    req = request(Wire(body), length=len(body))
    await _fingerprint(req)
    assert await read_capped(req, GLOBAL) == body  # the route's own cap is what applies


async def test_a_declared_length_over_the_global_ceiling_is_still_refused_unread() -> None:
    wire = Wire(b"x" * 10)
    with pytest.raises(ApiError) as caught:
        await _fingerprint(request(wire, length=GLOBAL + 1))
    assert caught.value.status == 413
    assert wire.sent == 0


async def test_a_stream_that_passes_the_small_ceiling_is_refused_and_not_held() -> None:
    wire = Wire(b"x" * 50_000_000)
    with pytest.raises(ApiError) as caught:
        await _fingerprint(request(wire))  # no length: it can only be read to be known
    assert caught.value.status == 413
    assert wire.sent <= SMALL + 1000


async def test_a_body_within_the_small_ceiling_keeps_its_fingerprint() -> None:
    body = b'{"tag": "one"}'
    req = request(Wire(body), length=len(body), kind=b"application/json")
    expected = hashlib.sha256()
    for part in (b"PUT", b"/v1/x", b"", body):
        expected.update(hashlib.sha256(part).digest())
    assert await _fingerprint(req) == expected.hexdigest()
    assert await req.body() == body


@pytest.fixture
async def anonymous(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    from app.core.http import router
    from app.main import mount

    r = router(prefix="/v1/test-order")

    @r.post("", status_code=201)
    async def probe() -> dict[str, str]:
        return {}

    mount(app, r)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test", headers={"origin": APP_ORIGIN}
    ) as client:
        yield client


async def test_a_request_with_no_valid_session_is_turned_away_before_its_body_is_read(
    anonymous: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.core.http as http

    seen: list[str] = []
    real = http._fingerprint

    async def spy(request: Request) -> str:
        seen.append("read")
        return await real(request)

    monkeypatch.setattr(http, "_fingerprint", spy)
    sent = anonymous.post(
        "/v1/test-order",
        content=b"x" * 3000,
        headers={"cookie": "av_session=not-a-session", CSRF_HEADER: "x", "idempotency-key": "k"},
    )
    response = await sent
    assert response.status_code == 401, response.text
    assert seen == []
