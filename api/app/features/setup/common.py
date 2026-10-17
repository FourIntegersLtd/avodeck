"""Small helpers shared by the slice C routes."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.config import cell_settings
from app.core.errors import ApiError
from app.core.packs import PackLibrary

CODE = r"^[a-z][a-z0-9_]{0,39}$"


def library(request: Request) -> PackLibrary:
    packs: PackLibrary = request.app.state.packs
    return packs


def cell(request: Request) -> str:
    region: str | None = request.app.state.region
    return region if region is not None else cell_settings().region


def invalid(*fields: str, message: str = "Check the highlighted fields.") -> ApiError:
    return ApiError("invalid", message, 422, fields=list(fields))


def not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


def registry_keys(items: list[Any]) -> list[str]:
    """Keys of a pack registry whose entries are either plain keys or `{key: ...}` mappings."""
    return [str(i["key"]) if isinstance(i, dict) else str(i) for i in items]
