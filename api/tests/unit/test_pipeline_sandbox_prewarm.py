"""Audit 13, DP-09 follow-up: the API process starts the parser sandbox with the pipeline's parsing module imported.

The worker names the module itself. The API runs the two-page-scan cut in the same sandbox, so the pipeline
registers it through its `on_startup(app)` hook (`app.core.features`): the children start with the module loaded and
the first split does not pay the import. The API holds few children: it cuts a page now and then, not a bundle.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.core import features as core_features
from app.core import sandbox
from app.core.config import SandboxSettings

pytestmark = pytest.mark.anyio


async def test_the_api_start_hook_starts_the_sandbox_with_the_parsing_module_prewarmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[tuple[SandboxSettings | None, tuple[str, ...]]] = []

    async def start(cfg: SandboxSettings | None = None, *, prewarm: Any = ()) -> None:
        started.append((cfg, tuple(prewarm)))

    monkeypatch.setattr(sandbox, "start", start)
    await core_features.start_features("app.features", object())

    assert started, "no feature started the sandbox when the API started"
    (cfg, prewarm), *rest = started
    assert not rest and prewarm == ("app.features.pipeline.parsing",)
    importlib.import_module(prewarm[0])  # a name that cannot be imported is skipped by the child, silently
    assert cfg is not None and cfg.pool_size <= 2, "the API keeps at most two parser children"
