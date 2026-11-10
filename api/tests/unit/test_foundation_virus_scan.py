"""Virus scanning (architecture 7 step 1, F3): one interface, a local stand-in in stage 1.

The stand-in flags the standard EICAR test file and passes everything else; the clamd client
arrives with the cloud cell. Files are scanned as a stream of chunks, as they sit in Storage.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.core.config import ConfigError
from app.core.scan import ClamdScanner, RecordedScanner, ScanResult, VirusScanner, scanner_from_env

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


CELL = {"RAILWAY_PROJECT_ID": "d1f0a1c2-0000-4000-8000-000000000000"}  # what Railway puts in every service


def test_off_railway_nothing_needs_setting() -> None:
    assert isinstance(scanner_from_env({"VIRUS_SCANNER": "recorded"}), RecordedScanner)
    assert isinstance(scanner_from_env({}), RecordedScanner)  # local, tests, CI: the stand-in
    assert isinstance(scanner_from_env({"CLAMD_HOST": "127.0.0.1"}), ClamdScanner)
    with pytest.raises(ConfigError):
        scanner_from_env({"VIRUS_SCANNER": "elsewhere"})


def test_a_cell_with_nothing_else_set_scans_with_clamav_at_the_fixed_host() -> None:
    scanner = scanner_from_env(CELL)
    assert isinstance(scanner, ClamdScanner)
    assert (scanner.host, scanner.port, scanner.timeout_s) == ("clamav.railway.internal", 3310, 60)
    assert isinstance(scanner_from_env({**CELL, "VIRUS_SCANNER": "clamd", "CLAMD_HOST": ""}), ClamdScanner)
    override = scanner_from_env({**CELL, "CLAMD_HOST": "clamd.other", "CLAMD_PORT": "3311"})
    assert isinstance(override, ClamdScanner)
    assert (override.host, override.port) == ("clamd.other", 3311)


def test_a_cell_refuses_the_stand_in_even_with_a_clamd_host() -> None:
    for extra in ({}, {"CLAMD_HOST": "clamav.railway.internal"}):
        with pytest.raises(ConfigError, match="VIRUS_SCANNER=recorded"):
            scanner_from_env({**CELL, "VIRUS_SCANNER": "recorded", **extra})
    with pytest.raises(ConfigError):
        scanner_from_env({**CELL, "VIRUS_SCANNER": "elsewhere"})


async def test_the_worker_stops_at_start_up_when_a_cell_asks_for_the_stand_in() -> None:
    from app.worker.main import run

    url = "postgresql://app_worker:x@127.0.0.1:1/postgres"
    env = {
        **CELL,
        "VIRUS_SCANNER": "recorded",
        "DATABASE_URL": url,
        "DATABASE_URL_DIRECT": url,
        "WORKER_QUEUES": "bulk",
    }
    with pytest.raises(ConfigError, match="VIRUS_SCANNER=recorded"):
        await run(env)


async def test_the_api_stops_at_start_up_when_a_cell_asks_for_the_stand_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from typing import Any

    from app.main import create_app

    monkeypatch.setenv("RAILWAY_PROJECT_ID", CELL["RAILWAY_PROJECT_ID"])
    monkeypatch.setenv("VIRUS_SCANNER", "recorded")
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
    with pytest.raises(ConfigError, match="VIRUS_SCANNER=recorded"):
        async with app.router.lifespan_context(app):
            pass
