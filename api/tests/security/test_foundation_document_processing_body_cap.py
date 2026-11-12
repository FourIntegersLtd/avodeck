"""AUDIT-13 DP-07: the routes that take a whole file as their body refuse an over-limit body without reading it all.

The two call sites (`setup/routes_files._body` for the template and the signature, `trial/common.pdf_body` for a
trial PDF) used to `await request.body()` and look at the size afterwards, so a body of any size was held in memory
first. A real ASGI `Request` is fed by a counting `receive`: the stream has no length, or a length that lies.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from app.core.errors import ApiError
from app.features.setup import routes_files
from app.features.trial import common

pytestmark = pytest.mark.anyio


class Wire:
    def __init__(self, total: int, chunk: int = 4096) -> None:
        self.total, self.chunk, self.sent = total, chunk, 0

    async def __call__(self) -> dict[str, Any]:
        n = min(self.chunk, self.total - self.sent)
        self.sent += n
        return {"type": "http.request", "body": b"%PDF" + b"x" * (n - 4) if self.sent == n else b"x" * n, "more_body": self.sent < self.total}


def request(wire: Wire, length: int | None) -> Request:
    headers = [(b"content-length", str(length).encode())] if length is not None else []
    return Request({"type": "http", "method": "PUT", "path": "/", "headers": headers, "query_string": b""}, wire)


@pytest.mark.parametrize("length", [None, 10])
async def test_the_setup_body_helper_stops_reading_at_its_limit(length: int | None) -> None:
    wire = Wire(50_000_000)
    with pytest.raises(ApiError) as caught:
        await routes_files._body(request(wire, length), 100_000)
    assert caught.value.status_code == 413
    assert wire.sent <= 100_000 + 8192, "no more than one chunk past the limit was read"


async def test_the_setup_body_helper_refuses_a_declared_over_limit_body_unread() -> None:
    wire = Wire(50_000_000)
    with pytest.raises(ApiError) as caught:
        await routes_files._body(request(wire, 50_000_000), 100_000)
    assert caught.value.status_code == 413 and wire.sent == 0


async def test_the_setup_body_helper_still_refuses_an_empty_body_and_accepts_a_small_one() -> None:
    with pytest.raises(ApiError) as caught:
        await routes_files._body(request(Wire(0), 0), 1000)
    assert caught.value.status_code == 422
    assert len(await routes_files._body(request(Wire(500), 500), 1000)) == 500


@pytest.mark.parametrize("length", [None, 10])
async def test_the_trial_pdf_body_stops_reading_at_its_limit(length: int | None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "MAX_PDF_BYTES", 100_000)
    wire = Wire(50_000_000)
    with pytest.raises(ApiError) as caught:
        await common.pdf_body(request(wire, length))
    assert caught.value.status_code == 422
    assert wire.sent <= 100_000 + 8192


async def test_the_trial_pdf_body_refuses_a_declared_over_limit_body_unread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "MAX_PDF_BYTES", 100_000)
    wire = Wire(50_000_000)
    with pytest.raises(ApiError):
        await common.pdf_body(request(wire, 50_000_000))
    assert wire.sent == 0


async def test_the_trial_pdf_body_still_accepts_a_pdf_and_refuses_a_non_pdf() -> None:
    assert (await common.pdf_body(request(Wire(500), 500))).startswith(b"%PDF")

    class NotPdf(Wire):
        async def __call__(self) -> dict[str, Any]:
            self.sent = self.total
            return {"type": "http.request", "body": b"hello", "more_body": False}

    with pytest.raises(ApiError):
        await common.pdf_body(request(NotPdf(5), 5))
