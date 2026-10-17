"""The slice C router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["setup"])

from app.features.setup import routes_declaration, routes_files, routes_profile  # noqa: E402, F401

__all__ = ["router"]
