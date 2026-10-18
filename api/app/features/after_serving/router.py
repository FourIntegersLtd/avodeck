"""The slice G router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["after_serving"])

from app.features.after_serving import (  # noqa: E402, F401
    events,
    jobs,
    routes_changes,
    routes_deadlines,
    routes_new_material,
    routes_questions,
    today,
)

__all__ = ["router"]
