"""The slice D router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["understand"])

from app.features.understand import (  # noqa: E402, F401
    jobs,
    routes_checks,
    routes_comments,
    routes_consultation,
    routes_findings,
    routes_guidelines,
    routes_links,
    routes_timeline,
    today,
)

__all__ = ["router"]
