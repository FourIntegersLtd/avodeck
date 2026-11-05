"""The API runs more than one process per replica (scalability PR 16): the process count comes from
`WEB_CONCURRENCY`, and each process's database pool size from `API_DB_POOL_MAX`.

Each process has its own pool, so the numbers are read from the environment and the connection budget in
`docs/runbooks/railway.md` is worked out from them (see `tests/guards/test_foundation_pool_budget.py`)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import ConfigError, database_settings
from app.main import api_database

REPO = Path(__file__).resolve().parents[3]
URL = {"DATABASE_URL": "postgresql://app_api:x@db.example:6543/postgres"}


def pool_max(env: dict[str, str]) -> int:
    return api_database(database_settings({**URL, **env}))._pool.max_size  # noqa: SLF001


def test_the_api_pool_defaults_to_ten_connections_per_process() -> None:
    assert database_settings(URL).api_pool_max == 10
    assert pool_max({}) == 10


def test_each_processs_pool_size_is_read_from_the_environment() -> None:
    assert pool_max({"API_DB_POOL_MAX": "8"}) == 8


def test_the_pool_floor_follows_a_small_maximum() -> None:
    assert pool_max({"API_DB_POOL_MAX": "1"}) == 1


@pytest.mark.parametrize("value", ["0", "-2", "many", "2.5"])
def test_a_pool_size_that_is_not_a_positive_whole_number_is_refused_by_name(value: str) -> None:
    with pytest.raises(ConfigError, match="API_DB_POOL_MAX"):
        database_settings({**URL, "API_DB_POOL_MAX": value})


def test_the_worker_pool_settings_are_not_the_apis() -> None:
    settings = database_settings({**URL, "API_DB_POOL_MAX": "8", "WORKER_DB_POOL_MAX": "6"})
    assert (settings.api_pool_max, settings.worker_pool_max) == (8, 6)


def test_the_image_starts_one_uvicorn_process_per_web_concurrency() -> None:
    """`--workers ${WEB_CONCURRENCY:-1}`: one process unless the service sets the variable."""
    dockerfile = (REPO / "api" / "Dockerfile").read_text()
    cmd = next(line for line in dockerfile.splitlines() if line.startswith("CMD"))
    assert "--workers ${WEB_CONCURRENCY:-1}" in cmd
    assert not re.search(r"--workers\s+\d", cmd)
    assert cmd.count("exec uvicorn") == 1  # SIGTERM still reaches uvicorn, which passes it to its processes


def test_the_env_example_names_both_settings() -> None:
    example = (REPO / "api" / ".env.example").read_text()
    assert re.search(r"^API_DB_POOL_MAX=", example, re.MULTILINE)
    assert re.search(r"^# ?WEB_CONCURRENCY=", example, re.MULTILINE)
