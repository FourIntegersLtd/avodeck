"""`core/sse.sse_stream` with a wake event (PR 8): fetch when told, with a slow safety tick as the net.

No database: a stand-in request carries the timings, `fetch` counts its calls, and the test sets the
wake event by hand, as the case notifier does when Postgres says a case's jobs changed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import pytest

from app.core.sse import sse_stream

pytestmark = pytest.mark.anyio


class _State:
    def __init__(self, *, limit: float, heartbeat: float, safety: float, min_fetch: float = 0.0) -> None:
        self.job_poll_seconds = 0.01  # must NOT be what drives a woken stream
        self.job_stream_seconds = limit
        self.job_heartbeat_seconds = heartbeat
        self.sse_safety_poll_seconds = safety
        self.sse_min_fetch_seconds = min_fetch


class _App:
    def __init__(self, state: _State) -> None:
        self.state = state


class _Request:
    def __init__(self, state: _State) -> None:
        self.app = _App(state)


class _Counter:
    def __init__(self) -> None:
        self.calls = 0
        self.times: list[float] = []

    async def fetch(self) -> dict[str, object] | None:
        self.calls += 1
        self.times.append(asyncio.get_running_loop().time())
        return {"n": self.calls}


def _watching(wake: asyncio.Event) -> tuple[Callable[[], AbstractContextManager[asyncio.Event]], dict[str, int]]:
    state = {"entered": 0, "left": 0}

    @contextmanager
    def watch() -> Iterator[asyncio.Event]:
        state["entered"] += 1
        try:
            yield wake
        finally:
            state["left"] += 1

    return watch, state


async def _run(resp: object) -> list[str]:
    return [chunk async for chunk in resp.body_iterator]  # type: ignore[attr-defined]


async def test_a_woken_stream_fetches_once_per_wake_and_not_on_a_timer() -> None:
    wake = asyncio.Event()
    watch, _ = _watching(wake)
    counter = _Counter()
    request = _Request(_State(limit=1.2, heartbeat=5.0, safety=30.0))
    resp = await sse_stream(request, counter.fetch, watch=watch)  # type: ignore[arg-type]
    task = asyncio.create_task(_run(resp))
    await asyncio.sleep(0.3)
    assert counter.calls == 1, "one fetch at connect and none while nothing happens"
    wake.set()
    await asyncio.sleep(0.2)
    assert counter.calls == 2, "one fetch for the notify"
    chunks = await asyncio.wait_for(task, 3)
    assert sum(c.startswith("data: ") for c in chunks) == 2
    assert counter.calls == 2


async def test_the_new_state_arrives_within_a_second_of_the_notify() -> None:
    wake = asyncio.Event()
    watch, _ = _watching(wake)
    counter = _Counter()
    request = _Request(_State(limit=1.5, heartbeat=5.0, safety=30.0, min_fetch=0.5))
    resp = await sse_stream(request, counter.fetch, watch=watch)  # type: ignore[arg-type]
    task = asyncio.create_task(_run(resp))
    await asyncio.sleep(0.6)  # past the first fetch's coalescing window
    sent = asyncio.get_running_loop().time()
    wake.set()
    await asyncio.sleep(0.9)
    assert counter.calls == 2
    assert counter.times[1] - sent < 1.0
    await asyncio.wait_for(task, 3)


async def test_with_no_notify_the_safety_tick_still_fetches_and_keepalives_still_go() -> None:
    wake = asyncio.Event()
    watch, _ = _watching(wake)
    counter = _Counter()
    request = _Request(_State(limit=1.0, heartbeat=0.2, safety=0.45))
    resp = await sse_stream(request, counter.fetch, watch=watch)  # type: ignore[arg-type]
    chunks = await asyncio.wait_for(_run(resp), 3)
    assert counter.calls == 3, "connect, then a safety tick at about 0.45 s and 0.9 s"
    assert sum(c.startswith(": keepalive") for c in chunks) >= 2, "keepalives are not tied to fetching"


async def test_a_burst_of_notifies_is_coalesced_to_one_fetch_per_window() -> None:
    wake = asyncio.Event()
    watch, _ = _watching(wake)
    counter = _Counter()
    request = _Request(_State(limit=1.3, heartbeat=5.0, safety=30.0, min_fetch=0.5))
    resp = await sse_stream(request, counter.fetch, watch=watch)  # type: ignore[arg-type]
    task = asyncio.create_task(_run(resp))
    await asyncio.sleep(0.05)
    for _ in range(20):
        wake.set()
        await asyncio.sleep(0.01)
    await asyncio.wait_for(task, 3)
    assert counter.calls == 2, "the connect fetch, plus one that covers the whole burst"
    assert counter.times[1] - counter.times[0] >= 0.49, "never more than one fetch per half second"


async def test_the_subscription_is_released_when_the_stream_ends() -> None:
    wake = asyncio.Event()
    watch, state = _watching(wake)
    counter = _Counter()
    request = _Request(_State(limit=0.2, heartbeat=5.0, safety=30.0))
    resp = await sse_stream(request, counter.fetch, watch=watch)  # type: ignore[arg-type]
    assert state["entered"] == 0, "nothing is subscribed until the body is read"
    await asyncio.wait_for(_run(resp), 3)
    assert state == {"entered": 1, "left": 1}


async def test_the_subscription_is_released_when_the_client_goes_away() -> None:
    wake = asyncio.Event()
    watch, state = _watching(wake)
    counter = _Counter()
    request = _Request(_State(limit=5.0, heartbeat=5.0, safety=30.0))
    resp = await sse_stream(request, counter.fetch, watch=watch)  # type: ignore[arg-type]
    body = resp.body_iterator.__aiter__()
    await body.__anext__()
    await body.aclose()  # type: ignore[attr-defined]
    assert state == {"entered": 1, "left": 1}


async def test_wake_within_caps_the_wait_so_a_caller_can_look_again_in_time() -> None:
    wake = asyncio.Event()
    watch, _ = _watching(wake)
    counter = _Counter()
    request = _Request(_State(limit=0.7, heartbeat=5.0, safety=30.0))
    resp = await sse_stream(request, counter.fetch, watch=watch, wake_within=0.25)  # type: ignore[arg-type]
    await asyncio.wait_for(_run(resp), 3)
    assert counter.calls >= 3, "fetched at connect and about every 0.25 s"


async def test_a_stream_whose_listener_is_down_falls_back_to_the_timed_loop() -> None:
    wake = asyncio.Event()
    watch, _ = _watching(wake)
    counter = _Counter()
    state = _State(limit=0.6, heartbeat=5.0, safety=30.0)
    state.job_poll_seconds = 0.1
    resp = await sse_stream(_Request(state), counter.fetch, watch=watch, live=lambda: False)  # type: ignore[arg-type]
    await asyncio.wait_for(_run(resp), 3)
    assert counter.calls >= 5, "a down listener must not leave the stream waiting out the 30 s safety tick"


async def test_the_stream_goes_back_to_waiting_when_the_listener_returns() -> None:
    wake = asyncio.Event()
    watch, _ = _watching(wake)
    counter = _Counter()
    up = {"live": False}
    state = _State(limit=1.2, heartbeat=5.0, safety=30.0)
    state.job_poll_seconds = 0.1
    resp = await sse_stream(_Request(state), counter.fetch, watch=watch, live=lambda: up["live"])  # type: ignore[arg-type]
    task = asyncio.create_task(_run(resp))
    await asyncio.sleep(0.45)
    up["live"] = True
    wake.set()  # what the notifier does on reconnect
    await asyncio.sleep(0.1)
    seen = counter.calls
    await asyncio.sleep(0.5)
    assert counter.calls == seen, "listening again: no more timed looks"
    await asyncio.wait_for(task, 3)
