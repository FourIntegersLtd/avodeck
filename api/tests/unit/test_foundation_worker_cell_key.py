"""The worker needs the cell key (audit 15, BG-16): deferred work is sealed under it, so a worker without it would
start, take its first mail and fail every row until the rows expired. It stops at start-up instead, naming the
setting (never its value), as it does for the virus scanner."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.core.config import ConfigError
from app.worker.main import check_cell_key, run

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]
KEY = base64.b64encode(b"k" * 32).decode()
CELL = {"RAILWAY_PROJECT_ID": "zz-project", "CLAMD_HOST": "clamav.railway.internal"}
URL = "postgresql://app_worker:x@127.0.0.1:1/postgres"


def test_a_missing_master_key_stops_the_worker_and_names_the_setting_only() -> None:
    with pytest.raises(ConfigError, match="LOCAL_MASTER_KEY"):
        check_cell_key({"KEY_PROVIDER": "local"})
    with pytest.raises(ConfigError, match="KEY_PROVIDER"):
        check_cell_key({"LOCAL_MASTER_KEY": KEY})
    with pytest.raises(ConfigError) as bad:
        check_cell_key({"KEY_PROVIDER": "local", "LOCAL_MASTER_KEY": "not base64 secret-value!"})
    assert "LOCAL_MASTER_KEY" in str(bad.value) and "secret-value" not in str(bad.value)


def test_a_worker_with_the_key_passes_the_check() -> None:
    check_cell_key({"KEY_PROVIDER": "local", "LOCAL_MASTER_KEY": KEY})


async def test_the_worker_stops_at_start_up_without_the_cell_key() -> None:
    env = {**CELL, "DATABASE_URL": URL, "DATABASE_URL_DIRECT": URL, "WORKER_QUEUES": "bulk", "KEY_PROVIDER": "local"}
    with pytest.raises(ConfigError, match="LOCAL_MASTER_KEY"):
        await run(env)


def test_the_cell_key_is_in_the_worker_environment_docs() -> None:
    railway = (REPO / "docs" / "runbooks" / "railway.md").read_text()
    assert "LOCAL_MASTER_KEY" in railway and "worker" in railway.split("LOCAL_MASTER_KEY", 1)[1][:400]
    example = (REPO / "api" / ".env.example").read_text()
    line = next(ln for ln in example.splitlines() if ln.startswith("LOCAL_MASTER_KEY"))
    assert "worker" in line
