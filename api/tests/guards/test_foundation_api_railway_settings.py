"""The api service's process count, pool size and draining time are set in railway/api.json, not by hand in the dashboard."""

import json
from pathlib import Path

DEPLOY = json.loads((Path(__file__).parents[3] / "railway" / "api.json").read_text())["deploy"]


def test_the_api_service_sets_two_processes_and_a_pool_of_eight() -> None:
    command = DEPLOY["startCommand"]
    assert "WEB_CONCURRENCY=2" in command
    assert "API_DB_POOL_MAX=8" in command
    assert "--workers ${WEB_CONCURRENCY}" in command


def test_the_api_service_drains_for_at_least_thirty_seconds() -> None:
    assert DEPLOY["drainingSeconds"] >= 30
