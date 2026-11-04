"""The worker holds database connections in two pools (its own, and the job queue library's), and both are
set from the environment. A hosted session pooler lets only so many clients in at once (15 by default), so
the defaults add up to less than that and the setting is documented next to the pooler's own number.

Without this the worker took 20 + 10 connections, the pooler refused the 16th client with
EMAXCONNSESSION, and jobs stopped. A page's model calls in flight follow the worker's pool, too."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import ConfigError
from app.core.db import Database
from app.worker.main import build_worker

URLS = {
    "DATABASE_URL": "postgresql://app_api:x@db.example:6543/postgres",
    "DATABASE_URL_DIRECT": "postgresql://app_worker:x@db.example:5432/postgres",
}
POOLER_DEFAULT_CLIENTS = 15


def pools(env: dict[str, str]) -> tuple[int, int]:
    worker: Any = build_worker({**URLS, **env})
    return worker.database._pool.max_size, worker.queue.connector._pool_args["max_size"]  # noqa: SLF001


def test_the_defaults_fit_inside_the_poolers_default_client_limit() -> None:
    own, queue = pools({})
    assert (own, queue) == (10, 4)
    assert own + queue <= POOLER_DEFAULT_CLIENTS


def test_the_workers_own_pool_size_is_read_from_the_environment() -> None:
    assert pools({"WORKER_DB_POOL_MAX": "7"})[0] == 7


def test_the_queue_pool_size_is_read_from_the_environment() -> None:
    assert pools({"WORKER_QUEUE_POOL_MAX": "3"})[1] == 3


@pytest.mark.parametrize("name", ["WORKER_DB_POOL_MAX", "WORKER_QUEUE_POOL_MAX"])
@pytest.mark.parametrize("value", ["0", "-2", "many"])
def test_a_pool_size_that_is_not_a_positive_whole_number_is_refused_by_name(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        build_worker({**URLS, name: value})


def test_a_database_says_how_large_its_pool_may_grow() -> None:
    database: Any = Database(URLS["DATABASE_URL"], max_size=6)
    assert database.max_size == 6


def test_a_small_queue_pool_is_never_asked_to_keep_more_than_it_may_hold() -> None:
    worker: Any = build_worker({**URLS, "WORKER_QUEUE_POOL_MAX": "1"})
    args = worker.queue.connector._pool_args  # noqa: SLF001
    assert args["min_size"] <= args["max_size"] == 1
