"""F42: a sleeping clamd service wakes when the worker connects; the client waits for it (ADR 0027).

Only the connect is retried (refused, unreachable, timed out). A failure after the stream has started raises at once,
because the chunk iterator cannot be replayed and the job retries from the start. Still fail closed.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator

import pytest

from app.core.config import ConfigError, clamd_settings
from app.core.scan import ClamdScanner, ScanResult, ScanUnavailable, scanner_from_env

pytestmark = pytest.mark.anyio

EICAR_REPLY = b"stream: Eicar-Test-Signature FOUND\0"


async def stream(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


class Sleeps:
    """An injected sleep that records the waits and does not really wait."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


async def read_instream(reader: asyncio.StreamReader) -> None:
    await reader.readexactly(10)
    while True:
        (size,) = struct.unpack(">I", await reader.readexactly(4))
        if size == 0:
            return
        await reader.readexactly(size)


async def free_port() -> int:
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    return port


async def eicar_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await read_instream(reader)
    writer.write(EICAR_REPLY)
    await writer.drain()
    writer.close()


def refuse_first(monkeypatch: pytest.MonkeyPatch, refusals: int) -> list[int]:
    """Make the first `refusals` connects fail as a sleeping service does; returns the call counter."""
    real_open = asyncio.open_connection
    calls = [0]

    async def flaky(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        calls[0] += 1
        if calls[0] <= refusals:
            raise ConnectionRefusedError
        return await real_open(host, port)

    monkeypatch.setattr(asyncio, "open_connection", flaky)
    return calls


async def test_connect_refused_twice_then_the_scan_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    server = await asyncio.start_server(eicar_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    calls = refuse_first(monkeypatch, 2)
    sleeps = Sleeps()
    try:
        result = await ClamdScanner("127.0.0.1", port, timeout_s=2, wake_s=120, sleep=sleeps).scan(stream(b"x"))
    finally:
        server.close()
        await server.wait_closed()
    assert result == ScanResult(clean=False, signature="Eicar-Test-Signature")
    assert len(sleeps.waits) == 2
    assert calls[0] == 3


async def test_connect_refused_beyond_the_budget_is_unavailable_within_the_budget() -> None:
    port = await free_port()
    sleeps = Sleeps()
    with pytest.raises(ScanUnavailable):
        await ClamdScanner("127.0.0.1", port, timeout_s=2, wake_s=30, sleep=sleeps).scan(stream(b"x"))
    assert sleeps.waits  # it did wait
    assert sum(sleeps.waits) <= 30


async def test_ping_waits_for_the_wake_too(monkeypatch: pytest.MonkeyPatch) -> None:
    async def pong(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readexactly(6)
        writer.write(b"PONG\0")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(pong, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    refuse_first(monkeypatch, 1)
    sleeps = Sleeps()
    try:
        await ClamdScanner("127.0.0.1", port, timeout_s=2, wake_s=120, sleep=sleeps).ping()
    finally:
        server.close()
        await server.wait_closed()
    assert len(sleeps.waits) == 1


async def test_a_failure_after_connecting_does_not_wait_or_retry() -> None:
    accepted = 0

    async def reset_mid_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal accepted
        accepted += 1
        await reader.readexactly(10)
        writer.transport.abort()

    server = await asyncio.start_server(reset_mid_stream, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    sleeps = Sleeps()

    async def big() -> AsyncIterator[bytes]:
        for _ in range(200):
            yield b"x" * 65536
            await asyncio.sleep(0)

    try:
        with pytest.raises(ScanUnavailable):
            await ClamdScanner("127.0.0.1", port, timeout_s=5, wake_s=120, sleep=sleeps).scan(big())
    finally:
        server.close()
        await server.wait_closed()
    assert sleeps.waits == []
    assert accepted == 1


def test_wake_budget_is_read_from_the_cell_config() -> None:
    assert clamd_settings({"CLAMD_HOST": "h"}).wake_s == 120
    assert clamd_settings({"CLAMD_HOST": "h", "CLAMD_WAKE_S": "45"}).wake_s == 45
    scanner = scanner_from_env({"VIRUS_SCANNER": "clamd", "CLAMD_HOST": "h", "CLAMD_WAKE_S": "45"})
    assert isinstance(scanner, ClamdScanner)
    assert scanner.wake_s == 45


def test_an_invalid_wake_budget_fails_start_up() -> None:
    for bad in ("soon", "-1"):
        with pytest.raises(ConfigError):
            scanner_from_env({"VIRUS_SCANNER": "clamd", "CLAMD_HOST": "h", "CLAMD_WAKE_S": bad})


def test_a_deployed_cell_waits_by_default() -> None:
    scanner = scanner_from_env({"RAILWAY_PROJECT_ID": "p"})
    assert isinstance(scanner, ClamdScanner)
    assert scanner.wake_s == 120
