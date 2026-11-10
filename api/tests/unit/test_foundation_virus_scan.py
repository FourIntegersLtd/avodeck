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
    with pytest.raises(ConfigError):  # nothing set: never the stand-in by default (audit 12, V1)
        scanner_from_env({})
    with pytest.raises(ConfigError):
        scanner_from_env({"VIRUS_SCANNER": "elsewhere"})


async def test_the_worker_stops_at_start_up_when_no_scanner_is_configured() -> None:
    from app.worker.main import run

    url = "postgresql://app_worker:x@127.0.0.1:1/postgres"
    env = {"DATABASE_URL": url, "DATABASE_URL_DIRECT": url, "WORKER_QUEUES": "bulk"}
    with pytest.raises(ConfigError, match="No virus scanner is configured"):
        await run(env)


def test_recorded_with_a_clamd_host_is_refused_without_naming_the_value() -> None:
    """A cell that copies api/.env.example and sets CLAMD_HOST must not silently run the stand-in (audit 12, V1)."""
    with pytest.raises(ConfigError, match="VIRUS_SCANNER=recorded") as caught:
        scanner_from_env({"VIRUS_SCANNER": "recorded", "CLAMD_HOST": "clamav.secret-host.internal"})
    assert "secret-host" not in str(caught.value)
    assert isinstance(scanner_from_env({"VIRUS_SCANNER": "recorded", "CLAMD_HOST": "  "}), RecordedScanner)


async def test_the_api_stops_at_start_up_when_no_scanner_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from typing import Any

    from app.main import create_app

    monkeypatch.delenv("VIRUS_SCANNER", raising=False)
    monkeypatch.delenv("CLAMD_HOST", raising=False)
    auth: Any = SimpleNamespace(
        settings=SimpleNamespace(
            app_origin="https://zz.invalid", app_origins=("https://zz.invalid",), cookie_domain=None
        )
    )
    app = create_app(
        database=SimpleNamespace(),  # type: ignore[arg-type]
        region="zz",
        auth=auth,
        storage=None,
        crypto=SimpleNamespace(),  # type: ignore[arg-type]
        features="app.worker",  # a package with no features inside
    )
    with pytest.raises(ConfigError, match="No virus scanner is configured"):
        async with app.router.lifespan_context(app):
            pass
