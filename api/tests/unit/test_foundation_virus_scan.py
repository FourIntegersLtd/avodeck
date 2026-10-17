"""Virus scanning (architecture 7 step 1, F3): one interface, a local stand-in in stage 1.

The stand-in flags the standard EICAR test file and passes everything else; the clamd client
arrives with the cloud cell. Files are scanned as a stream of chunks, as they sit in Storage.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.core.config import ConfigError
from app.core.scan import RecordedScanner, ScanResult, VirusScanner, scanner_from_env

pytestmark = pytest.mark.anyio

# The EICAR test string, built from parts so this file is not itself flagged by a scanner.
EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()


async def stream(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_a_clean_file_passes() -> None:
    scanner: VirusScanner = RecordedScanner()
    assert await scanner.scan(stream(b"%PDF-1.7 synthetic", b" page")) == ScanResult(clean=True, signature=None)


async def test_the_eicar_test_file_is_flagged_even_split_across_chunks() -> None:
    whole = await RecordedScanner().scan(stream(b"%PDF-1.7 ", EICAR, b" tail"))
    assert not whole.clean and whole.signature
    split = await RecordedScanner().scan(stream(b"start " + EICAR[:20], EICAR[20:41], EICAR[41:] + b" end"))
    assert not split.clean and split.signature == whole.signature


async def test_an_empty_file_is_clean() -> None:
    assert (await RecordedScanner().scan(stream())).clean


def test_the_cell_config_chooses_the_scanner() -> None:
    assert isinstance(scanner_from_env({"VIRUS_SCANNER": "recorded"}), RecordedScanner)
    with pytest.raises(ConfigError):
        scanner_from_env({})
    with pytest.raises(ConfigError):
        scanner_from_env({"VIRUS_SCANNER": "elsewhere"})
