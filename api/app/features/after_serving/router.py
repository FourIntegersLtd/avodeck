"""The slice G router. Route modules register on `router`; importing them here mounts them with it."""

from __future__ import annotations

import uuid

from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope

from app.core.http import router as _router


def _ids_are_uuids(base: type[APIRoute]) -> type[APIRoute]:
    """The router's route class, refusing a path whose `*_id` segment is not a UUID.

    So `PATCH /written-questions/import` does not match `PATCH /written-questions/{question_id}` (where sign-in
    would run first and answer 401); the literal path's own route answers 405 for the method it lacks. The
    path template is unchanged, so the OpenAPI schema and the guards read it as before (as slice I does).
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


router = _router(prefix="/v1", tags=["after_serving"])
router.route_class = _ids_are_uuids(router.route_class)

from app.features.after_serving import (  # noqa: E402, F401
    events,
    jobs,
    routes_changes,
    routes_deadlines,
    routes_new_material,
    routes_questions,
    routes_steps,
    today,
)

__all__ = ["router"]
