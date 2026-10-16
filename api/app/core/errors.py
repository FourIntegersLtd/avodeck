"""API errors as `{"error": {"code", "message"}}` (architecture 5.1).

Messages are fixed API text, never patient text and never the request's input; the web app
shows its own words for each `code` through i18n keys.
"""

from __future__ import annotations

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Raised by the database's state-machine triggers (migration 0002).
CONFLICT_SQLSTATE = "AV409"

_HTTP_CODES = {401: "not_signed_in", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(f"{status} {code}")
        self.code = code
        self.message = message
        self.status = status


def conflict() -> ApiError:
    return ApiError("conflict", "That change is not allowed in the current state.", 409)


def error_response(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status, exc.code, exc.message)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Field locations only: the rejected input may contain patient text.
    fields = [".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()]
    return error_response(422, "invalid", "Some details are not valid.", fields=fields)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return error_response(exc.status_code, _HTTP_CODES.get(exc.status_code, "error"), "Request refused.")


async def _database_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, psycopg.Error)
    if exc.sqlstate == CONFLICT_SQLSTATE:
        return await _api_error(request, conflict())
    raise exc


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(psycopg.Error, _database_error)
