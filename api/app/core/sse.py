"""Generic server-sent-events streaming (architecture 5.3): a shared loop any route can use to turn
a "fetch current state" function into a diff-based SSE stream, used first by `core/routes/jobs.py`.

The browser reaches a stream straight from the API's own origin (ADR 0031), so Railway's edge is the
only one in the way: it closes a request idle for 5 minutes, and community reports put a cutoff at
around a minute on a request that sends nothing at all. So the stream feeds the edge a comment frame
every `job_heartbeat_seconds` even when nothing has changed, and ends itself well before the idle
close so the browser reconnects on a clean end rather than on an edge error.

A caller supplies `fetch`, an async function returning the current state (or `None` once the
resource, or the caller's access to it, is gone) and, where the resource has a terminal state,
`is_finished`. Only a change from the last sent value is written as a `data:` event; everything else
is a heartbeat comment, never patient text (rule 6).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Request
from fastapi.responses import StreamingResponse

EDGE_IDLE_SECONDS = 300.0
EDGE_SILENT_SECONDS = 60.0
HEARTBEAT_SECONDS = 20.0
JOB_STREAM_SECONDS = 240.0


async def sse_stream(
    request: Request,
    fetch: Callable[[], Awaitable[dict[str, object] | None]],
    is_finished: Callable[[dict[str, object]], bool] = lambda _: False,
) -> StreamingResponse:
    poll = float(request.app.state.job_poll_seconds)
    limit = float(request.app.state.job_stream_seconds)
    heartbeat = float(request.app.state.job_heartbeat_seconds)

    async def events() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        last: dict[str, object] | None = None
        sent_at = loop.time()
        while True:
            current = await fetch()
            if current is None:  # the resource (or access to it) is gone
                return
            if current != last:
                last = current
                sent_at = loop.time()
                yield f"data: {json.dumps(current)}\n\n"
            elif loop.time() - sent_at >= heartbeat:
                # A comment frame: it keeps the connection fed without the browser seeing an event.
                sent_at = loop.time()
                yield ": keepalive\n\n"
            if is_finished(current) or loop.time() >= deadline:
                return
            await asyncio.sleep(min(poll, max(deadline - loop.time(), 0)))

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"cache-control": "no-store", "x-accel-buffering": "no"}
    )
