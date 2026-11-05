"""The identifier stripping in the request draft runs on a worker thread, never on the event loop (scalability PR 9)."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.core.ai import pii
from tests.repository.test_pipeline_requests import (  # noqa: F401  (fixtures)
    draft_path,
    storage,
    world,
)  # noqa: F401

pytestmark = pytest.mark.anyio


async def test_every_strip_in_the_draft_runs_off_the_event_loop(
    world: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = world["h"]
    on_loop: list[bool] = []
    original = pii.strip

    def spy(*args: Any, **kwargs: Any) -> Any:
        on_loop.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)

    monkeypatch.setattr(pii, "strip", spy)
    got = await h.client.post(draft_path(h), json={})
    assert got.status_code == 200, got.status_code
    assert on_loop, "the draft stripped nothing"
    assert not any(on_loop)
