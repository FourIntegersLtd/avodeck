"""Slice B routes: another reading of a page we couldn't read (T-10, ADR 0030). Added to the reader router.

The model's lines are shown beside the page for the expert to check; they never replace the expert's text and
never become facts, so these routes sit with the transcription action (`page.transcribe`).
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Literal

from fastapi import Depends, Query, Request
from pydantic import BaseModel

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.crypto import CaseCrypto
from app.core.errors import conflict
from app.core.http import CurrentScope, DbConn
from app.core.jobs import registry
from app.features.reader import second_reading
from app.features.reader.router import _page, router
from app.features.reader.schemas import JobOut

Transcribe = Annotated[CaseAccess, Depends(require_case_access("page.transcribe"))]


class SecondReadingLine(BaseModel):
    text: str
    confidence: float


class SecondReadingOut(BaseModel):
    status: Literal["none", "queued", "done", "refused", "failed"]
    reason: str | None = None
    lines: list[SecondReadingLine] = []


@router.post("/pages/{page_no}/second-reading", response_model=JobOut, status_code=202)
async def ask_second_reading(
    case_id: uuid.UUID,
    page_no: int,
    access: Transcribe,
    conn: DbConn,
    scope: CurrentScope,
    on_request: Annotated[bool, Query()] = False,
) -> JobOut:
    page = await _page(conn, case_id, page_no, scope)
    if not page["has_transcription"] and not on_request:
        raise conflict()
    await conn.execute(
        "insert into app.second_readings (case_id, page_id, requested_by, page_rotation) values (%s, %s, %s, %s)"
        " on conflict (page_id) do update set status = 'queued', reason = null, lines = null,"
        " requested_by = excluded.requested_by, page_rotation = excluded.page_rotation,"
        " requested_at = clock_timestamp(), finished_at = null",
        (case_id, page["id"], scope.person_id, page["rotation"]),
    )
    await audit.record(conn, action="page.second_reading_requested", target=f"page:{page['id']}", case_id=case_id)
    return JobOut(job_id=await registry.enqueue(conn, case_id, second_reading.KIND))


@router.get("/pages/{page_no}/second-reading", response_model=SecondReadingOut)
async def get_second_reading(
    case_id: uuid.UUID, page_no: int, access: Transcribe, request: Request, conn: DbConn, scope: CurrentScope
) -> SecondReadingOut:
    page = await _page(conn, case_id, page_no, scope)
    cur = await conn.execute("select status, reason, lines from app.second_readings where page_id = %s", (page["id"],))
    row = await cur.fetchone()
    if row is None:
        return SecondReadingOut(status="none")
    status, reason, sealed = row
    lines: list[SecondReadingLine] = []
    if status == "done" and sealed is not None:
        crypto: CaseCrypto = request.app.state.crypto
        data = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=second_reading.lines_purpose(page["id"]))
        lines = [SecondReadingLine(**ln) for ln in json.loads(data)]
    return SecondReadingOut(status=status, reason=reason, lines=lines)
