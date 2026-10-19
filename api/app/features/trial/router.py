"""The slice I router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

import uuid

from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope

from app.core.http import router as _router


def _ids_are_uuids(base: type[APIRoute]) -> type[APIRoute]:
    """The router's own route class, refusing a path whose `*_id` segment is not a UUID.

    So `PATCH /concordance/export` does not match `PATCH /concordance/{row_id}` (where sign-in would run
    first and answer 401); the literal path's own route answers 405 for the method it lacks. The path
    template stays `{row_id}`, so the OpenAPI schema and the guards read it as before.
    """

    class UuidIdRoute(base):  # type: ignore[valid-type, misc]
        def matches(self, scope: Scope) -> tuple[Match, Scope]:
            match, child = super().matches(scope)
            if match is not Match.NONE:
                for name, value in child.get("path_params", {}).items():
                    if name.endswith("_id"):
                        try:
                            uuid.UUID(str(value))
                        except ValueError:
                            return Match.NONE, {}
            return match, child

    return UuidIdRoute


router = _router(prefix="/v1", tags=["trial"])
router.route_class = _ids_are_uuids(router.route_class)

from app.features.trial import (  # noqa: E402, F401
    jobs,
    routes_concordance,
    routes_meetings,
    routes_offline,
    routes_pack,
    today,
)

__all__ = ["router"]
