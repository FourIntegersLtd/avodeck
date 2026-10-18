"""The slice F router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["report"])

from app.features.report import (  # noqa: E402, F401
    jobs,
    routes_build,
    routes_final_check,
    routes_sections,
    routes_serve,
    routes_sign,
    routes_time,
)

__all__ = ["router"]
