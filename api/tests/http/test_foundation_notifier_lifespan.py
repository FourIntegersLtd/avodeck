"""The app starts the case notifier only when DATABASE_URL_LISTEN is set (PR 8)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.core.config import ConfigError
from app.core.db import Database
from tests.support.files import build_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    return build_app(api_db, auth_env)


async def test_the_notifier_is_started_when_the_listen_url_is_set_and_stopped_with_the_app(
    app: FastAPI, api_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL_LISTEN", api_dsn)
    assert app.state.case_notifier is None
    async with app.router.lifespan_context(app):
        notifier = app.state.case_notifier
        assert notifier is not None
        await notifier.wait_ready(10)
        assert notifier.ready
    assert app.state.case_notifier is None
    assert not notifier.ready


async def test_without_a_listen_url_there_is_no_notifier_and_streams_keep_their_timed_loop(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL_LISTEN", raising=False)
    async with app.router.lifespan_context(app):
        assert app.state.case_notifier is None
    assert app.state.case_notifier is None


async def test_a_listen_url_on_the_transaction_mode_port_stops_start_up(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL_LISTEN", "postgresql://app_api:x@db.example:6543/postgres")
    with pytest.raises(ConfigError, match="DATABASE_URL_LISTEN"):
        async with app.router.lifespan_context(app):
            pass
