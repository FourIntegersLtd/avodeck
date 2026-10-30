"""Virus scanning (architecture 7 step 1, failure mode F3): one interface, a local stand-in.

Files are scanned as a stream of chunks, as they sit in Storage. The stand-in flags the standard
EICAR test file (also when it spans chunks) and passes everything else; `ClamdScanner` talks to
clamd (ADR 0026) with the zINSTREAM protocol. The cell config (`VIRUS_SCANNER`) chooses the scanner.
A reply that is not a clear verdict raises `ScanUnavailable`: callers fail closed.
"""

from __future__ import annotations

import asyncio
import os
import struct
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from app.core.config import ConfigError, clamd_settings

# Built from parts so this source file is not itself flagged.
_EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()
EICAR_SIGNATURE = "Eicar-Test-Signature"


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str | None = None


class ScanUnavailable(Exception):
    """The scanner could not be reached or gave no verdict. Never treated as clean: the caller fails
    closed, the job is retried and the document is not accepted (failure mode F3)."""


class VirusScanner(Protocol):
    async def scan(self, chunks: AsyncIterable[bytes]) -> ScanResult: ...


class ScanUnavailable(RuntimeError):
    """No clear verdict (clamd unreachable, timed out, over its size limit or a reply we do not know).

    The file must not be accepted. The message never carries file content.
    """


class ClamdScanner:
    """clamd over TCP, `zINSTREAM`: 4-byte big-endian length-prefixed chunks, then a zero-length chunk."""

    def __init__(self, host: str, port: int = 3310, timeout_s: float = 60.0) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s

    async def _exchange(self, send: Callable[[asyncio.StreamWriter], Awaitable[None]]) -> str:
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(self.timeout_s):
                reader, writer = await asyncio.open_connection(self.host, self.port)
                await send(writer)
                reply = await reader.read(4096)
        except (TimeoutError, OSError) as exc:
            raise ScanUnavailable(f"clamd did not answer ({type(exc).__name__})") from exc
        finally:
            if writer is not None:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()
        return reply.rstrip(b"\0\n").decode("utf-8", "replace").strip()

    async def scan(self, chunks: AsyncIterable[bytes]) -> ScanResult:
        async def send(writer: asyncio.StreamWriter) -> None:
            writer.write(b"zINSTREAM\0")
            async for chunk in chunks:
                if chunk:
                    writer.write(struct.pack(">I", len(chunk)) + chunk)
                    await writer.drain()
            writer.write(struct.pack(">I", 0))
            await writer.drain()

        try:
            reply = await self._exchange(send)
        except ConnectionError as exc:  # clamd closes the stream when its size limit is hit
            raise ScanUnavailable("clamd closed the connection") from exc
        if reply == "stream: OK":
            return ScanResult(clean=True)
        if reply.startswith("stream: ") and reply.endswith(" FOUND"):
            return ScanResult(clean=False, signature=reply[len("stream: ") : -len(" FOUND")])
        raise ScanUnavailable("clamd gave no clear verdict")

    async def ping(self) -> None:
        """Readiness: `zPING` must be answered `PONG`."""

        async def send(writer: asyncio.StreamWriter) -> None:
            writer.write(b"zPING\0")
            await writer.drain()

        if await self._exchange(send) != "PONG":
            raise ScanUnavailable("clamd did not answer PONG")


class RecordedScanner:
    async def scan(self, chunks: AsyncIterable[bytes]) -> ScanResult:
        tail = b""
        async for chunk in chunks:
            window = tail + chunk
            if _EICAR in window:
                return ScanResult(clean=False, signature=EICAR_SIGNATURE)
            tail = window[-(len(_EICAR) - 1) :]
        return ScanResult(clean=True)


def scanner_from_env(env: Mapping[str, str] | None = None) -> VirusScanner:
    """`VIRUS_SCANNER=clamd` (the cell) or `recorded` (tests and local)."""
    source = os.environ if env is None else env
    kind = source.get("VIRUS_SCANNER", "").strip()
    if kind == "recorded":
        return RecordedScanner()
    if kind == "clamd":
        settings = clamd_settings(source)
        return ClamdScanner(settings.host, settings.port, settings.timeout_s)
    raise ConfigError("VIRUS_SCANNER must be 'clamd' or 'recorded'")
