"""Virus scanning (architecture 7 step 1, failure mode F3): one interface, a local stand-in.

Files are scanned as a stream of chunks, as they sit in Storage. The stand-in flags the standard
EICAR test file (also when it spans chunks) and passes everything else; `ClamdScanner` talks to
clamd (ADR 0027) with the zINSTREAM protocol. In a deployed cell the scanner is always ClamAV (`scanner_from_env`).
A reply that is not a clear verdict raises `ScanUnavailable`: callers fail closed.

The clamd service may sleep when idle and wakes when something connects (ADR 0027, F42). While it starts, connects are
refused, so `ClamdScanner` retries only the connect, with back-off, for up to the wake budget (`CLAMD_WAKE_S`, default
120 s). Once connected, a failure raises at once: the chunk iterator cannot be replayed, so the job retries from the
start.
"""

from __future__ import annotations

import asyncio
import logging
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

log = logging.getLogger(__name__)

# Waits between connect attempts while the service wakes; the last value repeats until the wake budget is used.
WAKE_DELAYS_S = (2.0, 4.0, 8.0, 15.0)


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str | None = None


class VirusScanner(Protocol):
    async def scan(self, chunks: AsyncIterable[bytes]) -> ScanResult: ...


class ScanUnavailable(RuntimeError):
    """No clear verdict (clamd unreachable, timed out, over its size limit or a reply we do not know).

    The file must not be accepted. The message never carries file content.
    """


class ClamdScanner:
    """clamd over TCP, `zINSTREAM`: 4-byte big-endian length-prefixed chunks, then a zero-length chunk."""

    def __init__(
        self,
        host: str,
        port: int = 3310,
        timeout_s: float = 60.0,
        wake_s: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self.wake_s = wake_s  # `scanner_from_env` sets the cell's budget; 0 means do not wait
        self._sleep = sleep

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connect, waiting with back-off (up to `wake_s` in all) while the service is not up yet."""
        waited = 0.0
        attempt = 0
        while True:
            attempt += 1
            try:
                async with asyncio.timeout(self.timeout_s):
                    return await asyncio.open_connection(self.host, self.port)
            except (TimeoutError, OSError) as exc:
                delay = min(WAKE_DELAYS_S[min(attempt - 1, len(WAKE_DELAYS_S) - 1)], self.wake_s - waited)
                if delay <= 0:
                    log.warning("clamd connect failed (%s), attempt %d, giving up", type(exc).__name__, attempt)
                    raise ScanUnavailable(f"clamd did not answer ({type(exc).__name__})") from exc
                log.info("clamd connect failed (%s), attempt %d, waiting %.0f s", type(exc).__name__, attempt, delay)
                await self._sleep(delay)
                waited += delay

    async def _exchange(self, send: Callable[[asyncio.StreamWriter], Awaitable[None]]) -> str:
        reader, writer = await self._connect()
        try:
            async with asyncio.timeout(self.timeout_s):
                await send(writer)
                reply = await reader.read(4096)
        except (TimeoutError, OSError) as exc:
            raise ScanUnavailable(f"clamd did not answer ({type(exc).__name__})") from exc
        finally:
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


# A deployed cell is a Railway service (ADR 0021): Railway puts this variable in every service's environment, so nobody
# sets it by hand and a cell cannot be built without it. ClamAV's place in the cell is fixed by ADR 0027.
CELL_MARKER = "RAILWAY_PROJECT_ID"
CELL_CLAMD_HOST = "clamav.railway.internal"
CELL_CLAMD_PORT = 3310
CELL_CLAMD_TIMEOUT_S = 60


def in_deployed_cell(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return bool(source.get(CELL_MARKER, "").strip())


def scanner_from_env(env: Mapping[str, str] | None = None) -> VirusScanner:
    """The scanner this process runs.

    In a deployed cell (Railway's own `RAILWAY_PROJECT_ID` is present) it is always ClamAV at the fixed private host;
    CLAMD_HOST, CLAMD_PORT, CLAMD_TIMEOUT_S and CLAMD_WAKE_S (default 120) may override the defaults.
    `VIRUS_SCANNER=recorded` there raises: the stand-in flags only the EICAR test string and would record every other
    file as clean. Anywhere else (local, tests, CI) it is clamd when CLAMD_HOST is set, else the recorded stand-in;
    nothing needs setting.
    """
    source = os.environ if env is None else env
    kind = source.get("VIRUS_SCANNER", "").strip()
    cell = in_deployed_cell(source)
    if cell:
        if kind == "recorded":
            raise ConfigError(
                "VIRUS_SCANNER=recorded is for tests and local work: a deployed cell always scans with ClamAV"
            )
        kind = kind or "clamd"
        source = {
            "CLAMD_HOST": CELL_CLAMD_HOST,
            "CLAMD_PORT": str(CELL_CLAMD_PORT),
            "CLAMD_TIMEOUT_S": str(CELL_CLAMD_TIMEOUT_S),
            **{k: v for k, v in source.items() if k.startswith("CLAMD_") and v.strip()},
        }
    elif not kind:
        kind = "clamd" if source.get("CLAMD_HOST", "").strip() else "recorded"
    if kind == "recorded":
        return RecordedScanner()
    if kind == "clamd":
        settings = clamd_settings(source)
        return ClamdScanner(settings.host, settings.port, settings.timeout_s, settings.wake_s)
    raise ConfigError("VIRUS_SCANNER must be 'clamd', 'recorded' or unset (auto-detect)")
