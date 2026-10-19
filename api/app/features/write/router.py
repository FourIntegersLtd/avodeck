"""The slice E router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["write"])

from app.features.write import (  # noqa: E402, F401
    jobs,
    routes_lint,
    routes_opinion,
    routes_points,
    routes_progress,
    routes_range_note,
    routes_witness,
    routes_workspace,
    today,
)

__all__ = ["router"]
