"""The API stops the sandbox pool when it shuts down, so no parser child is left behind (audit 13, F-h)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core import sandbox
from app.main import create_app

pytestmark = pytest.mark.anyio


class Storage(SimpleNamespace):
    async def ensure_bucket(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def an_app() -> Any:
    auth = SimpleNamespace(
        settings=SimpleNamespace(
            app_origin="https://zz.invalid", app_origins=("https://zz.invalid",), cookie_domain=None
        )
    )
    return create_app(
        database=SimpleNamespace(),  # type: ignore[arg-type]
        region="zz",
        auth=auth,  # type: ignore[arg-type]
        storage=Storage(),  # type: ignore[arg-type]
        crypto=SimpleNamespace(),  # type: ignore[arg-type]
        features="app.worker",  # a package with no features inside
    )


@pytest.fixture
def stops(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def stop() -> None:
        calls.append("stop")

    monkeypatch.setattr(sandbox, "stop", stop)
    return calls


async def test_shutting_down_stops_the_sandbox(stops: list[str]) -> None:
    app = an_app()
    async with app.router.lifespan_context(app):
        assert stops == []
    assert stops == ["stop"]


async def test_the_sandbox_is_stopped_even_when_the_app_ends_with_an_error(stops: list[str]) -> None:
    app = an_app()
    with pytest.raises(RuntimeError):
        async with app.router.lifespan_context(app):
            raise RuntimeError("zz")
    assert stops == ["stop"]
