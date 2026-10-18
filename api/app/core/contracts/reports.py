"""Report versions (architecture 4, rule 8): slice F builds, locks and serves them; G and H read them.

Once `locked_at` is set the row is immutable; the database refuses any change except
locked -> served and served -> withdrawn (SQLSTATE `AVLCK`), and the API answers 409 `locked`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from app.core.errors import ApiError, locked

__all__ = ["AFTER_SERVING_TYPES", "ReportVersion", "ReportVersionStatus", "ensure_unlocked", "locked"]

ReportVersionStatus = Literal["draft", "locked", "served", "withdrawn"]

# Version types slice G writes after serving (answers to written questions, supplementary reports). Slice F's
# "latest draft" and "newest version" skip them: F builds, checks and signs only its own report.
AFTER_SERVING_TYPES: tuple[str, ...] = ("answers", "supplementary")


class ReportVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    case_id: uuid.UUID
    version_no: int
    type: str
    display_name: str
    docx_path: str | None = None
    pdf_path: str | None = None
    sha256: str | None = None
    status: ReportVersionStatus
    locked_at: datetime | None = None
    withdrawn_at: datetime | None = None
    withdrawn_reason: str | None = None
    prev_version_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


async def ensure_unlocked(conn: AsyncConnection, report_version_id: uuid.UUID) -> None:
    """404 if the person cannot see it, 409 `locked` if it is locked; before any work on a draft."""
    cur = await conn.execute("select locked_at from app.report_versions where id = %s", (report_version_id,))
    row = await cur.fetchone()
    if row is None:
        raise ApiError("not_found", "Not found.", 404)
    if row[0] is not None:
        raise locked()
