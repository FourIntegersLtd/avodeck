"""Virus scanning (architecture 7 step 1, failure mode F3): one interface, a local stand-in.

Files are scanned as a stream of chunks, as they sit in Storage. The stand-in flags the standard
EICAR test file (also when it spans chunks) and passes everything else; the clamd client arrives
with the cloud cell. The cell config (`VIRUS_SCANNER`) chooses the scanner.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from app.core.config import ConfigError

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
    """`VIRUS_SCANNER=recorded`. The clamd client arrives with the cloud cell."""
    source = os.environ if env is None else env
    kind = source.get("VIRUS_SCANNER", "").strip()
    if kind != "recorded":
        raise ConfigError("VIRUS_SCANNER must be 'recorded' until clamd is configured")
    return RecordedScanner()
