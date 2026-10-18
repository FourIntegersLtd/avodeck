"""GET /v1/jobs/{job_id} and /v1/jobs/{job_id}/events: a job's progress (architecture 5.3, screen 07).

Progress is status, step code, percentage, time left and an error code: never patient text (rule 6).
The events stream sends a change only when something changed, ends when the job is done or failed,
and ends after `app.state.job_stream_seconds` so the browser reconnects (EventSource does this itself).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import Request
from fastapi.responses import StreamingResponse
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core.auth.access import check_case_access
from app.core.db import Database, RequestScope
from app.core.errors import ApiError
from app.core.http import CurrentScope, DbConn, router

jobs_router = router(prefix="/v1/jobs", tags=["jobs"])

_PROGRESS = ("status", "step", "pct", "eta_seconds", "error_code")  # the stream keeps these
_FINISHED = {"done", "failed"}


class Job(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    kind: str
    status: str
    step: str | None
    pct: int | None
    eta_seconds: int | None
    step_done: int | None = None
    step_total: int | None = None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


async def _job(conn: AsyncConnection, job_id: uuid.UUID) -> dict[str, object]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, case_id, kind, status, step, pct, eta_seconds, step_done, step_total, error_code, created_at,"
            " started_at, finished_at from app.jobs where id = %s",
            (job_id,),
        )
        row = await cur.fetchone()
    if row is None or row["case_id"] is None:  # jobs without a case are not shown to anyone
        raise ApiError("not_found", "Not found.", 404)
    await check_case_access(conn, row["case_id"], "case.view_metadata")
    return row


@jobs_router.get("/{job_id}", response_model=Job)
async def get_job(job_id: uuid.UUID, conn: DbConn) -> Job:
    return Job(**await _job(conn, job_id))  # type: ignore[arg-type]


async def _progress(database: Database, scope: RequestScope, job_id: uuid.UUID) -> dict[str, object] | None:
    async with database.request(scope) as conn:
        cur = await conn.execute(
            "select status, step, pct, eta_seconds, error_code from app.jobs where id = %s", (job_id,)
        )
        row = await cur.fetchone()
    return None if row is None else dict(zip(_PROGRESS, row, strict=True))


@jobs_router.get("/{job_id}/events", response_class=StreamingResponse)
async def job_events(job_id: uuid.UUID, request: Request, conn: DbConn, scope: CurrentScope) -> StreamingResponse:
    await _job(conn, job_id)
    database: Database = request.app.state.database
    poll = float(request.app.state.job_poll_seconds)
    limit = float(request.app.state.job_stream_seconds)

    async def events() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        last: dict[str, object] | None = None
        while True:
            current = await _progress(database, scope, job_id)
            if current is None:  # the job (or access to its case) is gone
                return
            if current != last:
                last = current
                yield f"data: {json.dumps(current)}\n\n"
            if current["status"] in _FINISHED or loop.time() >= deadline:
                return
            await asyncio.sleep(min(poll, max(deadline - loop.time(), 0)))

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"cache-control": "no-store", "x-accel-buffering": "no"}
    )
