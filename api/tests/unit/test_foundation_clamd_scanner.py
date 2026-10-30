"""F3: the clamd client (zINSTREAM) against a fake clamd, so no daemon is needed.

A verdict maps to a result; anything that is not a clear verdict raises `ScanUnavailable`
so a file is never accepted without a clean answer.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest

from app.core.config import ConfigError, clamd_settings
from app.core.scan import ClamdScanner, ScanResult, ScanUnavailable, scanner_from_env

pytestmark = pytest.mark.anyio

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


async def stream(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def read_instream(reader: asyncio.StreamReader) -> bytes:
    assert await reader.readexactly(10) == b"zINSTREAM\0"
    body = b""
    while True:
        (size,) = struct.unpack(">I", await reader.readexactly(4))
        if size == 0:
            return body
        body += await reader.readexactly(size)


def replying(reply: bytes, received: list[bytes] | None = None) -> Handler:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        body = await read_instream(reader)
        if received is not None:
            received.append(body)
        writer.write(reply)
        await writer.drain()
        writer.close()

    return handle


async def serve(handler: Handler) -> tuple[asyncio.Server, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def scan_with(reply: bytes, *parts: bytes) -> ScanResult:
    server, port = await serve(replying(reply))
    try:
        return await ClamdScanner("127.0.0.1", port, timeout_s=2).scan(stream(*parts))
    finally:
        server.close()
        await server.wait_closed()


async def test_ok_reply_is_clean() -> None:
    assert await scan_with(b"stream: OK\0", b"%PDF-1.7", b" page") == ScanResult(clean=True)


async def test_the_whole_stream_arrives_in_length_prefixed_chunks() -> None:
    received: list[bytes] = []
    server, port = await serve(replying(b"stream: OK\0", received))
    try:
        await ClamdScanner("127.0.0.1", port, timeout_s=2).scan(stream(b"abc", b"", b"defg"))
    finally:
        server.close()
    assert received == [b"abcdefg"]


async def test_found_reply_is_infected_with_the_signature() -> None:
    result = await scan_with(b"stream: Win.Test.EICAR_HDB-1 FOUND\0", b"x")
    assert result == ScanResult(clean=False, signature="Win.Test.EICAR_HDB-1")


async def test_size_limit_exceeded_is_unavailable() -> None:
    with pytest.raises(ScanUnavailable):
        await scan_with(b"INSTREAM size limit exceeded. ERROR\0", b"x")


async def test_an_unknown_reply_is_unavailable() -> None:
    with pytest.raises(ScanUnavailable):
        await scan_with(b"stream: Something odd ERROR\0", b"x")


async def test_an_empty_reply_is_unavailable() -> None:
    with pytest.raises(ScanUnavailable):
        await scan_with(b"", b"x")


async def test_connection_refused_is_unavailable() -> None:
    server, port = await serve(replying(b""))
    server.close()
    await server.wait_closed()
    with pytest.raises(ScanUnavailable):
        await ClamdScanner("127.0.0.1", port, timeout_s=2).scan(stream(b"x"))


async def test_a_silent_server_times_out_as_unavailable() -> None:
    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_instream(reader)
        await asyncio.sleep(5)

    server, port = await serve(silent)
    try:
        with pytest.raises(ScanUnavailable):
            await ClamdScanner("127.0.0.1", port, timeout_s=0.2).scan(stream(b"x"))
    finally:
        server.close()


async def test_ping_answers_pong() -> None:
    async def pong(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert await reader.readexactly(6) == b"zPING\0"
        writer.write(b"PONG\0")
        await writer.drain()
        writer.close()

    server, port = await serve(pong)
    try:
        await ClamdScanner("127.0.0.1", port, timeout_s=2).ping()
    finally:
        server.close()


async def test_ping_without_a_server_is_unavailable() -> None:
    server, port = await serve(replying(b""))
    server.close()
    await server.wait_closed()
    with pytest.raises(ScanUnavailable):
        await ClamdScanner("127.0.0.1", port, timeout_s=2).ping()


def test_scanner_from_env_builds_clamd() -> None:
    env = {"VIRUS_SCANNER": "clamd", "CLAMD_HOST": "clamav.railway.internal", "CLAMD_PORT": "3310"}
    assert isinstance(scanner_from_env(env), ClamdScanner)


def test_clamd_settings_read_the_cell_config() -> None:
    settings = clamd_settings({"CLAMD_HOST": "h", "CLAMD_PORT": "3311", "CLAMD_TIMEOUT_S": "30"})
    assert (settings.host, settings.port, settings.timeout_s) == ("h", 3311, 30.0)
    assert clamd_settings({"CLAMD_HOST": "h"}).port == 3310


def test_clamd_needs_a_host() -> None:
    with pytest.raises(ConfigError):
        clamd_settings({})
    with pytest.raises(ConfigError):
        scanner_from_env({"VIRUS_SCANNER": "clamd"})


def test_an_unknown_scanner_still_fails() -> None:
    with pytest.raises(ConfigError):
        scanner_from_env({"VIRUS_SCANNER": "other"})
