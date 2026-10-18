"""Slice F, report and sign: build the report, the final check, sign, serve, time and invoices.

`router` (built in `router.py`) is mounted by `app.main.create_app()` with `mount()`. Importing it here loads
every route module and job kind in one order, whichever module of the slice is imported first.
"""

from __future__ import annotations

from app.features.report.router import router

__all__ = ["router"]
