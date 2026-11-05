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
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager, aclosing, nullcontext

from fastapi import Request
from fastapi.responses import StreamingResponse

EDGE_IDLE_SECONDS = 300.0
EDGE_SILENT_SECONDS = 60.0
HEARTBEAT_SECONDS = 20.0
JOB_STREAM_SECONDS = 240.0
MIN_FETCH_SECONDS = 0.5  # a woken stream looks at most this often, however many notifications arrive
DEFAULT_SSE_SAFETY_POLL_SECONDS = 30.0  # the net under a lost notification (config: SSE_SAFETY_POLL_SECONDS)


async def sse_stream(
    request: Request,
    fetch: Callable[[], Awaitable[dict[str, object] | None]],
    is_finished: Callable[[dict[str, object]], bool] = lambda _: False,
    poll_factor: float = 1.0,
    *,
    watch: Callable[[], AbstractContextManager[asyncio.Event]] | None = None,
    wake_within: float | None = None,
) -> StreamingResponse:
    """Stream `fetch()` as it changes.

    Without `watch` the loop looks every `job_poll_seconds` (tests, local). With `watch` (a case notifier's
    subscription, entered when the body starts and left when it ends) the loop waits to be woken instead: it
    fetches at connect, once per wake (never more than once per `sse_min_fetch_seconds`), and at least every
    `sse_safety_poll_seconds` (or `wake_within`, if shorter, so a caller can re-check access in time).
    Keepalives go every heartbeat either way.
    """
    state = request.app.state
    poll = float(state.job_poll_seconds) * poll_factor
    limit = float(state.job_stream_seconds)
    heartbeat = float(state.job_heartbeat_seconds)
    safety = float(getattr(state, "sse_safety_poll_seconds", DEFAULT_SSE_SAFETY_POLL_SECONDS))
    if wake_within is not None:
        safety = min(safety, wake_within)
    min_fetch = float(getattr(state, "sse_min_fetch_seconds", MIN_FETCH_SECONDS))

    async def events() -> AsyncIterator[str]:
        with watch() if watch is not None else nullcontext() as wake:
            async with aclosing(_frames(wake)) as frames:
                async for frame in frames:
                    yield frame

    async def _frames(wake: asyncio.Event | None) -> AsyncGenerator[str]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        last: dict[str, object] | None = None
        sent_at = loop.time()
        fetched_at: float | None = None
        woken = False
        while True:
            now = loop.time()
            if wake is None or fetched_at is None or woken or now - fetched_at >= safety:
                if wake is not None:
                    if fetched_at is not None and now - fetched_at < min_fetch:
                        await asyncio.sleep(min_fetch - (now - fetched_at))  # coalesce a burst into one look
                    wake.clear()  # before the look, so a change during it wakes the next wait
                current = await fetch()
                fetched_at = loop.time()
                if current is None:  # the resource (or access to it) is gone
                    return
                if current != last:
                    last = current
                    sent_at = loop.time()
                    yield f"data: {json.dumps(current)}\n\n"
                if is_finished(current):
                    return
            if loop.time() - sent_at >= heartbeat:
                # A comment frame: it keeps the connection fed without the browser seeing an event.
                sent_at = loop.time()
                yield ": keepalive\n\n"
            now = loop.time()
            if now >= deadline:
                return
            if wake is None:
                await asyncio.sleep(min(poll, max(deadline - now, 0)))
                continue
            wait = min(
                deadline - now,
                heartbeat - (now - sent_at),
                (fetched_at + safety) - now if fetched_at is not None else 0.0,
            )
            woken = False
            try:
                await asyncio.wait_for(wake.wait(), timeout=max(wait, 0.0))
                woken = True
            except TimeoutError:
                pass

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"cache-control": "no-store", "x-accel-buffering": "no"}
    )
