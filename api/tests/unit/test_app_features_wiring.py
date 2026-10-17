"""Slice features plug into the app and the worker (F1, docs/slices/0-foundation.md "Changes for slice C").

`app.features.setup` exposes `router` (built with `app.core.http.router()`); `create_app()` finds and
mounts it by convention (`app.core.features`) and the worker loads the package so its job kinds and
Today providers register. Checked by behaviour, not by the source text, since main finds features itself.
"""

from __future__ import annotations

import sys


def test_the_setup_feature_exposes_a_router() -> None:
    from fastapi import APIRouter

    from app.features.setup import router

    assert isinstance(router, APIRouter)
    assert router.prefix == "/v1"


def test_the_app_imports_and_mounts_the_setup_feature() -> None:
    from app.features.setup import router
    from app.main import create_app

    app = create_app(region="zz")
    assert "app.features.setup" in sys.modules
    mounted = {getattr(r, "path", None) for r in app.routes}
    wanted = {getattr(r, "path", None) for r in router.routes}
    assert "/v1/cases/{case_id}/questions" in wanted and wanted <= mounted


def test_the_worker_imports_the_setup_feature() -> None:
    from app.core.features import load_features
    from app.core.jobs import registry

    load_features()
    assert "app.features.setup.jobs" in sys.modules
    assert "extract_questions" in registry.kinds
