"""Slice B routes: another reading of a page we couldn't read (T-10, ADR 0030). Added to the reader router.

The model's lines are shown beside the page for the expert to check; they never replace the expert's text and
never become facts, so these routes sit with the transcription action (`page.transcribe`).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Query, Request
from pydantic import BaseModel, Field

from app.core import audit, rate_limits
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


ASK_PER_MINUTE = 6  # stricter than the general limit: each ask can cost a model call (audit step 14, R-E-2)
_ACTIVE = ("queued", "running", "paused")


class SecondReadingOut(BaseModel):
    status: Literal["none", "queued", "done", "refused", "failed"]
    reason: str | None = None
    lines: list[SecondReadingLine] = []
    source: Literal["machine_read"] = Field(
        default="machine_read", description="The lines were read by the software from the picture, not checked."
    )
    prompt_version: str | None = Field(default=None, description="The version of the instructions that made them.")
    read_at: datetime | None = Field(default=None, description="When the software finished reading the page.")


async def _job_for(conn: DbConn, case_id: uuid.UUID, *, active_only: bool) -> uuid.UUID | None:
    """The case's newest second-reading job (one still waiting or running when `active_only`)."""
    cur = await conn.execute(
        "select id from app.jobs where case_id = %s and kind = %s and (not %s or status = any(%s))"
        " order by created_at desc limit 1",
        (case_id, second_reading.KIND, active_only, list(_ACTIVE)),
    )
    row = await cur.fetchone()
    return None if row is None else row[0]


async def _picture_sha(request: Request, page: dict) -> str | None:  # type: ignore[type-arg]
    """The hash of the page picture as stored now (its sealed bytes), so the job can tell if it was redrawn."""
    storage = getattr(request.app.state, "storage", None)
    if storage is None or page["image_path"] is None:
        return None
    return hashlib.sha256(await storage.get(page["image_path"])).hexdigest()


@router.post("/pages/{page_no}/second-reading", response_model=JobOut, status_code=202)
async def ask_second_reading(
    case_id: uuid.UUID,
    page_no: int,
    access: Transcribe,
    request: Request,
    conn: DbConn,
    scope: CurrentScope,
    on_request: Annotated[bool, Query()] = False,
) -> JobOut:
    page = await _page(conn, case_id, page_no, scope)
    if not page["has_transcription"] and not on_request:
        raise conflict()
    if int(getattr(request.app.state, "api_rate_per_minute", 0)) > 0:
        await rate_limits.check(conn, f"POST {request.scope['route'].path_format}", ASK_PER_MINUTE)
    cur = await conn.execute("select status from app.second_readings where page_id = %s", (page["id"],))
    have = await cur.fetchone()
    if have is not None and have[0] in ("queued", "done"):
        # One reading per page at a time (R-E-2): waiting or finished, asking again queues nothing and answers
        # with the job already there. A refused or failed one may be asked for again.
        existing = await _job_for(conn, case_id, active_only=have[0] == "queued")
        if existing is not None:
            return JobOut(job_id=existing)
    await conn.execute(
        "insert into app.second_readings (case_id, page_id, requested_by, page_rotation, image_sha256)"
        " values (%s, %s, %s, %s, %s)"
        " on conflict (page_id) do update set status = 'queued', reason = null, lines = null,"
        " requested_by = excluded.requested_by, page_rotation = excluded.page_rotation,"
        " image_sha256 = excluded.image_sha256,"
        " requested_at = clock_timestamp(), finished_at = null",
        (case_id, page["id"], scope.person_id, page["rotation"], await _picture_sha(request, page)),
    )
    await audit.record(conn, action="page.second_reading_requested", target=f"page:{page['id']}", case_id=case_id)
    return JobOut(job_id=await registry.enqueue(conn, case_id, second_reading.KIND))


@router.get("/pages/{page_no}/second-reading", response_model=SecondReadingOut, response_model_exclude_unset=True)
async def get_second_reading(
    case_id: uuid.UUID, page_no: int, access: Transcribe, request: Request, conn: DbConn, scope: CurrentScope
) -> SecondReadingOut:
    page = await _page(conn, case_id, page_no, scope)
    cur = await conn.execute(
        "select status, reason, lines, prompt_version, finished_at from app.second_readings where page_id = %s",
        (page["id"],),
    )
    row = await cur.fetchone()
    if row is None:
        return SecondReadingOut(status="none", reason=None, lines=[])
    status, reason, sealed, version, finished = row
    lines: list[SecondReadingLine] = []
    if status == "done" and sealed is not None:
        crypto: CaseCrypto = request.app.state.crypto
        data = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=second_reading.lines_purpose(page["id"]))
        lines = [SecondReadingLine(**ln) for ln in json.loads(data)]
    if status != "done":
        return SecondReadingOut(status=status, reason=reason, lines=[])
    # Only lines that exist carry the marker: they are read by the software, not checked (R-O-5).
    return SecondReadingOut(
        status=status, reason=reason, lines=lines, source="machine_read", prompt_version=version, read_at=finished
    )
