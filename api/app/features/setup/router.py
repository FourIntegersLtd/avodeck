"""The slice C router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

from app.core.http import router as _router

router = _router(prefix="/v1", tags=["setup"])

from app.features.setup import (  # noqa: E402, F401
    jobs,
    routes_case,
    routes_conflicts,
    routes_declaration,
    routes_documents,
    routes_files,
    routes_org_template,
    routes_prefill,
    routes_profile,
    routes_questions,
    today,
)

__all__ = ["router"]
