"""Slice C, case setup (docs/slices/C-setup.md). Owned by setup-owner.

`router` is mounted by `app.main.create_app()` with `mount()`. Add routes to it (or replace it with one
built in `router.py` and re-exported here) and import the modules that register job kinds
(`@registry.kind`) and Today providers (`@needs_you.provider`) from this file, so both the API and the
worker know them.
"""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["setup"])

__all__ = ["router"]
