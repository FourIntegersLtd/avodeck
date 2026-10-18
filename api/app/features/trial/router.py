"""The slice I router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["trial"])

from app.features.trial import (  # noqa: E402, F401
    jobs,
    routes_concordance,
    routes_meetings,
    routes_offline,
    routes_pack,
    today,
)

__all__ = ["router"]
