"""core/db.py keeps a few connections open and warm, so a request never waits to connect."""

from __future__ import annotations

from app.core.db import Database

DSN = "postgresql://app_api:x@db.example:6543/postgres"


def test_the_pool_keeps_four_connections_warm_by_default() -> None:
    assert Database(DSN)._pool.min_size == 4  # noqa: SLF001


def test_a_small_pool_is_never_asked_to_keep_more_than_it_may_hold() -> None:
    assert Database(DSN, max_size=1)._pool.min_size == 1  # noqa: SLF001


def test_the_api_pool_stays_at_ten_connections_by_default() -> None:
    assert Database(DSN)._pool.max_size == 10  # noqa: SLF001


def test_the_worker_pool_may_grow_to_twenty_connections() -> None:
    from app.worker.main import build_worker

    worker = build_worker(
        {
            "DATABASE_URL": "postgresql://app_api:x@db.example:6543/postgres",
            "DATABASE_URL_DIRECT": "postgresql://app_worker:x@db.example:5432/postgres",
        }
    )
    assert worker.database._pool.max_size == 20  # noqa: SLF001


def test_an_explicit_maximum_is_kept() -> None:
    assert Database(DSN, max_size=2)._pool.max_size == 2  # noqa: SLF001


def test_an_explicit_minimum_is_kept() -> None:
    assert Database(DSN, min_size=2, max_size=6)._pool.min_size == 2  # noqa: SLF001
