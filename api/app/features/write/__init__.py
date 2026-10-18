"""Slice E, write: points for and against, range notes, witness statements, the opinion, citations, lint.

`router` (built in `router.py`) is mounted by `app.main.create_app()` with `mount()`. Importing it here loads
every route module and job kind in one order, whichever module of the slice is imported first.
"""

from __future__ import annotations

from app.features.write.router import router

__all__ = ["router"]
