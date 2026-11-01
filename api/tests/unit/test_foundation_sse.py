"""`core/sse.sse_stream`: the generic diff/heartbeat/deadline loop used by every SSE route.

No database or app factory needed here: `sse_stream` only reads the three timing settings off
`request.app.state`, so a bare stand-in request exercises the loop on its own.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.sse import sse_stream

pytestmark = pytest.mark.anyio


class _State:
    def __init__(self, poll: float, limit: float, heartbeat: float) -> None:
        self.job_poll_seconds = poll
        self.job_stream_seconds = limit
        self.job_heartbeat_seconds = heartbeat


class _App:
    def __init__(self, state: _State) -> None:
        self.state = state


class _Request:
    def __init__(self, state: _State) -> None:
        self.app = _App(state)


async def _collect(resp: object) -> list[str]:
    return [chunk async for chunk in resp.body_iterator]  # type: ignore[attr-defined]


async def test_a_change_is_sent_as_a_data_event() -> None:
    values = iter([{"status": "running", "pct": 0}, {"status": "running", "pct": 50}, {"status": "done", "pct": 100}])

    async def fetch() -> dict[str, object] | None:
        return next(values, {"status": "done", "pct": 100})

    request = _Request(_State(poll=0.01, limit=2.0, heartbeat=5.0))
    resp = await sse_stream(request, fetch, lambda current: current["status"] == "done")  # type: ignore[arg-type]
    chunks = await asyncio.wait_for(_collect(resp), 2)
    sent = [c for c in chunks if c.startswith("data: ")]
    assert len(sent) == 3
    assert '"pct": 50' in sent[1]
    assert sent[-1].count('"status": "done"') == 1


async def test_an_unchanged_value_sends_a_keepalive_comment_not_an_event() -> None:
    async def fetch() -> dict[str, object] | None:
        return {"status": "running"}

    request = _Request(_State(poll=0.02, limit=0.2, heartbeat=0.05))
    resp = await sse_stream(request, fetch)  # type: ignore[arg-type]
    chunks = await asyncio.wait_for(_collect(resp), 2)
    assert chunks[0].startswith("data: "), "the first value is still new, so it is sent"
    assert any(c.startswith(": keepalive") for c in chunks[1:])
    assert all(not c.startswith("data: ") for c in chunks[1:]), "no further change happened"


async def test_the_stream_ends_when_fetch_returns_none() -> None:
    calls = {"n": 0}

    async def fetch() -> dict[str, object] | None:
        calls["n"] += 1
        return {"status": "running"} if calls["n"] == 1 else None

    request = _Request(_State(poll=0.01, limit=5.0, heartbeat=5.0))
    resp = await sse_stream(request, fetch)  # type: ignore[arg-type]
    chunks = await asyncio.wait_for(_collect(resp), 2)
    assert chunks == ['data: {"status": "running"}\n\n']


async def test_the_stream_ends_when_is_finished_is_true() -> None:
    async def fetch() -> dict[str, object] | None:
        return {"status": "done"}

    request = _Request(_State(poll=0.01, limit=5.0, heartbeat=5.0))
    resp = await sse_stream(request, fetch, lambda current: current["status"] == "done")  # type: ignore[arg-type]
    chunks = await asyncio.wait_for(_collect(resp), 2)
    assert chunks == ['data: {"status": "done"}\n\n']


async def test_the_stream_ends_at_its_deadline_even_with_no_change_and_no_finish() -> None:
    async def fetch() -> dict[str, object] | None:
        return {"status": "running"}

    request = _Request(_State(poll=0.05, limit=0.1, heartbeat=5.0))
    resp = await sse_stream(request, fetch)  # type: ignore[arg-type]
    chunks = await asyncio.wait_for(_collect(resp), 2)
    assert chunks, "the initial value is still sent before the deadline stops the loop"
