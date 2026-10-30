"""F3 against a real clamd (CI service container). Skipped when clamd is not reachable."""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator

import pytest

from app.core.scan import ClamdScanner

pytestmark = [pytest.mark.anyio, pytest.mark.clamd]

HOST = os.environ.get("CLAMD_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAMD_PORT", "3310"))

# The EICAR test string, built from parts so this file is not itself flagged by a scanner.
EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()


def clamd_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def need_clamd() -> None:
    if not clamd_reachable():
        pytest.skip("clamd is not reachable (set CLAMD_HOST and CLAMD_PORT)")


async def stream(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_real_clamd_flags_eicar_and_passes_a_clean_file() -> None:
    scanner = ClamdScanner(HOST, PORT, timeout_s=30)
    await scanner.ping()
    found = await scanner.scan(stream(b"%PDF-1.7 ", EICAR))
    assert not found.clean and found.signature
    assert (await scanner.scan(stream(b"%PDF-1.7 synthetic page"))).clean
