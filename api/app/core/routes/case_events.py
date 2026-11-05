"""GET /v1/cases/{case_id}/events: one live stream of a case's background jobs (architecture 5.3).

Every wait in the app (a bundle being read, a report being built, an export, a letter being read) is a
job on a case, so one stream per open case tells every screen when its job moves on: the browser keeps a
single connection per case instead of one per screen or per job.

It sends the case's jobs that are still going, and those that ended in the last ten minutes, as status,
step, counts and an error code: never patient text (rule 6). Access is checked at connect and again at
least every `ACCESS_RECHECK_SECONDS` (rule 9), so a person removed from the case stops hearing about it.
With a case notifier running (`DATABASE_URL_LISTEN`) the stream looks when a job on the case changes and
on a slow safety tick; without one it keeps the timed loop. Reads between access checks still run under
row-level security as the person, so they see nothing the person may not. The router is `touch=False`: a
stream left open is not activity, so it must not hold off the idle sign-out.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import StreamingResponse

from app.core.auth.access import CaseAccess, check_case_access, require_case_access
from app.core.db import Database, RequestScope
from app.core.errors import ApiError
from app.core.http import CurrentScope, router
from app.core.notify import CaseNotifier
from app.core.sse import ACCESS_RECHECK_SECONDS, AccessRecheck, sse_stream

case_events_router = router(prefix="/v1/cases", tags=["jobs"], touch=False)

RECENT_MINUTES = 10  # how long a finished job stays in the list, so a screen that opens late still sees it end
MAX_JOBS = 50
__all__ = ["ACCESS_RECHECK_SECONDS", "case_events_router"]  # the limit lives in core.sse
POLL_FACTOR = 2.0  # looks half as often as a single job's stream: the whole case changes more rarely to a screen

_FIELDS = ("id", "kind", "status", "step", "pct", "eta_seconds", "step_done", "step_total", "error_code")


async def _jobs(
    database: Database, scope: RequestScope, case_id: uuid.UUID, *, check_access: bool = True
) -> dict[str, object] | None:
    async with database.request(scope) as conn:
        if check_access:
            try:
                await check_case_access(conn, case_id, "case.view_metadata")
            except ApiError:
                return None  # access is gone: the stream ends
        cur = await conn.execute(
            "select id::text, kind, status, step, pct, eta_seconds, step_done, step_total, error_code"
            " from app.jobs where case_id = %s"
            " and (finished_at is null or finished_at > now() - make_interval(mins => %s))"
            " order by created_at desc, id limit %s",
            (case_id, RECENT_MINUTES, MAX_JOBS),
        )
        rows = await cur.fetchall()
    return {"jobs": [dict(zip(_FIELDS, row, strict=True)) for row in rows]}


@case_events_router.get("/{case_id}/events", response_class=StreamingResponse)
async def case_events(
    case_id: uuid.UUID,
    request: Request,
    _access: Annotated[CaseAccess, Depends(require_case_access("case.view_metadata"))],
    scope: CurrentScope,
) -> StreamingResponse:
    database: Database = request.app.state.database
    notifier: CaseNotifier | None = getattr(request.app.state, "case_notifier", None)
    recheck = AccessRecheck(request.app.state, notifier)  # `_access` above is the check at connect

    async def fetch() -> dict[str, object] | None:
        return await _jobs(database, scope, case_id, check_access=recheck.due())

    if notifier is None:
        return await sse_stream(request, fetch, poll_factor=POLL_FACTOR)
    return await sse_stream(
        request,
        fetch,
        poll_factor=POLL_FACTOR,
        watch=lambda: notifier.watch(case_id),
        wake_within=recheck.interval,
        live=lambda: notifier.ready,
    )
