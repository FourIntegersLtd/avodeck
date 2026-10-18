"""The slice E router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["write"])

from app.features.write import jobs, routes_opinion, routes_points, routes_range_note  # noqa: E402, F401

__all__ = ["router"]
