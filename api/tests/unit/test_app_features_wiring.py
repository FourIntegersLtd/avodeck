"""Slice features plug into the app and the worker (F1, docs/slices/0-foundation.md "Changes for slice C").

`app.features.setup` exposes `router` (built with `app.core.http.router()`); `create_app()` mounts it
with `mount()` and the worker imports the package so its job kinds and Today providers register.
"""

from __future__ import annotations

import importlib
import inspect
import sys


def test_the_setup_feature_exposes_a_router() -> None:
    from fastapi import APIRouter

    from app.features.setup import router

    assert isinstance(router, APIRouter)
    assert router.prefix == "/v1"


def test_the_app_imports_and_mounts_the_setup_feature() -> None:
    import app.main

    assert "app.features.setup" in sys.modules
    source = inspect.getsource(app.main.create_app)
    assert "setup_router" in source


def test_the_worker_imports_the_setup_feature() -> None:
    worker = importlib.import_module("app.worker.main")
    assert "app.features.setup" in sys.modules
    assert "app.features.setup" in inspect.getsource(worker)
