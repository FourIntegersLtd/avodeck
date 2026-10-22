"""The slice O router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["organisations"])

from app.features.organisations import (  # noqa: E402, F401
    routes_billing,
    routes_invites,
    routes_members,
    routes_orgs,
)

__all__ = ["router"]
