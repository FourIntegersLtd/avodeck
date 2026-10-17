"""Slice C, case setup (docs/slices/C-setup.md). Owned by setup-owner.

`router` (built in `router.py`) is mounted by `app.main.create_app()` with `mount()`. Modules that register
job kinds (`@registry.kind`) and Today providers (`@needs_you.provider`) are imported from here, so both
the API and the worker know them.
"""

from __future__ import annotations

from app.features.setup.router import router

__all__ = ["router"]
