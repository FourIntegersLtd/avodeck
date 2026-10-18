"""The guideline version in force on a date (architecture 5.2, slice D brief).

Labels, dates and links only, from the case's overlay pack (`pack.guidelines()`); never guideline
text. The caller names the case, so the version comes from that case's packs (404 if they cannot see it).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import Request
from pydantic import BaseModel

from app.core.auth.access import check_case_access
from app.core.http import DbConn
from app.features.understand.common import case_pack, not_found
from app.features.understand.guidelines import in_force
from app.features.understand.router import router


class InForce(BaseModel):
    code: str
    title: str | None
    version_label: str
    in_force_from: date
    in_force_to: date | None
    url: str | None


@router.get("/guidelines/in-force", response_model=InForce)
async def guideline_in_force(code: str, date: date, case_id: uuid.UUID, request: Request, conn: DbConn) -> InForce:
    await check_case_access(conn, case_id, "case.view_content")
    pack = await case_pack(request, conn, case_id)
    row = in_force(pack.guidelines(), code, date)
    if row is None:
        raise not_found()
    return InForce(
        code=str(row["code"]),
        title=row.get("title"),
        version_label=str(row["version_label"]),
        in_force_from=row["in_force_from"],
        in_force_to=row.get("in_force_to"),
        url=row.get("url"),
    )
