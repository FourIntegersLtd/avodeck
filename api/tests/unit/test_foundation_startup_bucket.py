"""Start-up makes sure the private storage bucket exists (architecture 2); a failure never stops the app.

The failure is logged by its error type only: an error's message could carry a path or a name (rule 6).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.main import create_app

pytestmark = pytest.mark.anyio


class Storage(SimpleNamespace):
    async def ensure_bucket(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        return None


def an_app(storage: Any) -> Any:
    auth = SimpleNamespace(
        settings=SimpleNamespace(
            app_origin="https://zz.invalid", app_origins=("https://zz.invalid",), cookie_domain=None
        )
    )
    return create_app(
        database=SimpleNamespace(),  # type: ignore[arg-type]
        region="zz",
        auth=auth,  # type: ignore[arg-type]
        storage=storage,
        crypto=SimpleNamespace(),  # type: ignore[arg-type]
        features="app.worker",  # a package with no features inside
    )


async def test_start_up_creates_the_bucket_once() -> None:
    storage = Storage(calls=0, error=None)
    app = an_app(storage)
    async with app.router.lifespan_context(app):
        assert storage.calls == 1


async def test_a_failing_bucket_check_logs_only_the_error_type_and_start_up_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = Storage(calls=0, error=RuntimeError("zz-secret-path /cases/1234/page.pdf"))
    app = an_app(storage)
    with caplog.at_level(logging.WARNING):
        async with app.router.lifespan_context(app):
            assert storage.calls == 1
    text = caplog.text
    assert "RuntimeError" in text
    assert "zz-secret-path" not in text and "1234" not in text
    assert all(r.exc_info is None and not r.exc_text for r in caplog.records)


async def test_an_app_whose_cell_has_no_storage_still_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "STORAGE_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    app = an_app(None)
    async with app.router.lifespan_context(app):
        assert app.state.storage is None
