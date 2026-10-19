"""API errors as `{"error": {"code", "message"}}` (architecture 5.1).

Messages are fixed API text, never patient text and never the request's input; the web app
shows its own words for each `code` through i18n keys.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match, Route

# Raised by the database's state-machine triggers (migration 0002).
CONFLICT_SQLSTATE = "AV409"
# Raised when something tries to change a locked report version (migration 0008, rule 8).
LOCKED_SQLSTATE = "AVLCK"

logger = logging.getLogger("app.errors")

_HTTP_CODES = {401: "not_signed_in", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int, **extra: object) -> None:
        super().__init__(f"{status} {code}")
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra


class ProcessingPaused(ApiError):
    """Work stops safely and waits (F5 back-off spent, F11 cost guard, F24 model or key service gone).

    `reason` is a fixed code: `service_busy`, `model_unavailable`, `cost_guard`, `key_service_unreachable`.
    Nothing is retried in another region or with another model.
    """

    def __init__(self, reason: str) -> None:
        super().__init__("processing_paused", "Processing is paused. Your work is safe.", 503, reason=reason)
        self.reason = reason


def conflict() -> ApiError:
    return ApiError("conflict", "That change is not allowed in the current state.", 409)


def already_confirmed() -> ApiError:
    return ApiError("already_confirmed", "That question is already confirmed.", 409)


def questions_locked() -> ApiError:
    return ApiError("questions_locked", "The questions are locked.", 409)


def locked() -> ApiError:
    return ApiError("locked", "This report version is locked. Changes go in a supplementary report.", 409)


def error_response(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status, exc.code, exc.message, **exc.extra)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Field locations only: the rejected input may contain patient text.
    fields = [".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()]
    return error_response(422, "invalid", "Some details are not valid.", fields=fields)


def _allowed_methods(request: Request) -> str:
    """Every method the routes for this path offer (Starlette names only the first matching route's).

    A literal path (`/v1/me/sessions/revoke-all`) is its own resource, so it does not also advertise a
    template that happens to match it (`/v1/me/sessions/{session_id}`).
    """
    matching = [
        route
        for route in request.app.router.routes
        if isinstance(route, Route) and route.methods and route.matches(request.scope)[0] != Match.NONE
    ]
    literal = [route for route in matching if "{" not in route.path]
    methods: set[str] = set()
    for route in literal or matching:
        methods |= route.methods or set()
    return ", ".join(sorted(methods))


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, "error")
    headers = dict(exc.headers or {})
    if exc.status_code == 405:
        headers["Allow"] = _allowed_methods(request) or headers.get("Allow", "")
    response = error_response(exc.status_code, code, "Request refused.")
    response.headers.update(headers)
    return response


async def _database_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, psycopg.Error)
    if exc.sqlstate == CONFLICT_SQLSTATE:
        return await _api_error(request, conflict())
    if exc.sqlstate == LOCKED_SQLSTATE:
        return await _api_error(request, locked())
    raise exc


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Any other failure: 500 with a fixed message. Logged with its type and stack only (core/logs.py)."""
    route = getattr(request.scope.get("route"), "path", "unmatched")
    logger.error("unhandled %s on %s %s", type(exc).__name__, request.method, route, exc_info=exc)
    return error_response(500, "server_error", "Something went wrong. Please try again.")


ERROR_SCHEMAS: dict[str, Any] = {
    "ApiErrorDetail": {
        "title": "ApiErrorDetail",
        "type": "object",
        "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
        "required": ["code", "message"],
        "additionalProperties": True,
    },
    "ApiError": {
        "title": "ApiError",
        "type": "object",
        "properties": {"error": {"$ref": "#/components/schemas/ApiErrorDetail"}},
        "required": ["error"],
    },
}
_DEFAULT_ERROR = {
    "description": "Error: the `code` says which (architecture 5.1)",
    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}},
}
_METHODS = {"get", "put", "post", "patch", "delete"}


def document_errors(app: FastAPI) -> None:
    """Give every operation the shared error body as its `default` response, so clients know it."""
    build = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        doc = build()
        doc.setdefault("components", {}).setdefault("schemas", {}).update(ERROR_SCHEMAS)
        for item in doc.get("paths", {}).values():
            for method, op in item.items():
                if method in _METHODS:
                    op.setdefault("responses", {}).setdefault("default", _DEFAULT_ERROR)
        app.openapi_schema = doc
        return doc

    app.openapi = openapi  # type: ignore[method-assign]


def install_error_handlers(app: FastAPI) -> None:
    document_errors(app)
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(psycopg.Error, _database_error)
    app.add_exception_handler(Exception, _unexpected_error)
