"""Small helpers shared by the slice D routes."""

from __future__ import annotations

import uuid

from fastapi import Request
from psycopg import AsyncConnection

from app.core.errors import ApiError
from app.core.packs import MergedPack, PackLibrary


def invalid(*fields: str, message: str = "Check the highlighted fields.") -> ApiError:
    return ApiError("invalid", message, 422, fields=list(fields))


def undated_entry(page_no: int) -> ApiError:
    return ApiError(
        "undated_entry",
        "This entry has no visit date yet.",
        422,
        fields=["fact_id"],
        page_no=page_no,
    )


def not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


def forbidden() -> ApiError:
    return ApiError("forbidden", "Only the person allowed can do this.", 403)


async def case_pack(request: Request, conn: AsyncConnection, case_id: uuid.UUID) -> MergedPack:
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    packs: PackLibrary = request.app.state.packs
    return packs.combine(str(row[0]), str(row[1]), str(row[2]))
