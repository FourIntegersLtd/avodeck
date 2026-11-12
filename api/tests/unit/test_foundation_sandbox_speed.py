"""The parser sandbox must not make a call wait for a child to start (audit 13, DP-09 follow-up).

A child that imports the pipeline's parsing module takes about 2 s to start. These tests use `tests.support.slow_import`
(1.5 s to import) as the stand-in: a new child must be started and warmed in the background, before the old one is
given up, so no call pays for it; `start()` can name modules each child imports before it is ready. They also cover
the pool under several event loops.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import pytest

from app.core import sandbox
from app.core.config import SandboxSettings, sandbox_settings
from app.core.sandbox import run
from tests.support import sandbox_fns as fns

SLOW = "tests.support.slow_import"
FAST = 0.6  # a warm call takes a few milliseconds; the child start takes 1.5 s and more


def settings(**kw: Any) -> SandboxSettings:
    base: dict[str, Any] = {
        "pool_size": 1,
        "timeout_seconds": 20.0,
        "memory_bytes": 512 * 1024 * 1024,
        "recycle_after": 50,
    }
    return SandboxSettings(**{**base, **kw})


async def timed(*args: Any, **kw: Any) -> tuple[Any, float]:
    started = time.monotonic()
    result = await run(*args, **kw)
    return result, time.monotonic() - started


@pytest.mark.anyio
async def test_modules_named_at_start_are_imported_in_each_child_before_its_first_call() -> None:
    await sandbox.start(settings(), prewarm=[SLOW])
    try:
        await asyncio.sleep(3.0)  # the child starts in the background when the pool starts
        loaded, took = await timed(fns.slow_module_loaded)
    finally:
        await sandbox.stop()
    assert loaded is True
    assert took < FAST, "the first call did not wait for the import"


@pytest.mark.anyio
async def test_a_module_that_cannot_be_imported_does_not_stop_the_child_or_the_calls() -> None:
    await sandbox.start(settings(), prewarm=["no_such_module_anywhere"])
    try:
        assert await run(fns.add, 1, 2) == 3
    finally:
        await sandbox.stop()


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["", "-m", "a b", "os; import sys", "../x"])
async def test_a_module_name_that_is_not_a_dotted_name_is_refused(name: str) -> None:
    with pytest.raises(ValueError):
        await sandbox.start(settings(), prewarm=[name])
    await sandbox.stop()


@pytest.mark.anyio
async def test_recycling_never_makes_a_call_wait_for_the_new_child() -> None:
    await sandbox.start(settings(recycle_after=2), prewarm=[SLOW])
    try:
        await asyncio.sleep(3.0)
        first, _ = await timed(fns.pid)
        await run(fns.pid)
        third, took = await timed(fns.pid)  # the replacement is still importing (1.5 s)
        assert took < FAST, "the call after the recycle point waited for the replacement"
        assert third == first, "the old child went on serving until the replacement was ready"
        await asyncio.sleep(3.0)
        fourth, took = await timed(fns.pid)
    finally:
        await sandbox.stop()
    assert fourth != first, "the warmed replacement took over"
    assert took < FAST


@pytest.mark.anyio
async def test_a_child_killed_by_a_timeout_is_replaced_in_the_background() -> None:
    await sandbox.start(settings(), prewarm=[SLOW])
    try:
        await asyncio.sleep(3.0)
        with pytest.raises(sandbox.SandboxError):
            await run(fns.sleep_for, 60, timeout=0.5)
        await asyncio.sleep(3.0)
        loaded, took = await timed(fns.slow_module_loaded)
    finally:
        await sandbox.stop()
    assert loaded is True
    assert took < FAST, "the call after the kill did not wait for a new child to start"


@pytest.mark.anyio
async def test_stop_leaves_no_child_even_one_still_starting() -> None:
    await sandbox.start(settings(pool_size=2), prewarm=[SLOW])
    await asyncio.sleep(0.3)
    await sandbox.stop()
    await asyncio.sleep(0.2)
    assert not sandbox._live


def test_the_default_pool_follows_the_cpu_count_with_a_cap_and_recycles_rarely() -> None:
    s = sandbox_settings({})
    assert s.pool_size == min(4, max(2, os.cpu_count() or 2))
    assert s.recycle_after == 500
    assert sandbox_settings({"SANDBOX_POOL_SIZE": "7"}).pool_size == 7


# --- one pool, several event loops (each `asyncio.run` is a new loop) ----------------------------------------


def test_the_pool_works_under_a_second_event_loop_when_calls_have_to_queue() -> None:
    async def burst() -> list[int]:
        return list(await asyncio.gather(*(run(fns.sleep_for, 0.2) for _ in range(4))))

    asyncio.run(sandbox.start(settings(pool_size=1)))
    try:
        assert len(asyncio.run(burst())) == 4  # the first loop that has to queue
        assert len(asyncio.run(burst())) == 4  # a second one: the queue must not belong to the first
        assert len(asyncio.run(burst())) == 4
    finally:
        asyncio.run(sandbox.stop())
    assert not sandbox._live


def test_children_of_a_finished_loop_do_not_pile_up() -> None:
    async def one() -> int:
        return await run(fns.pid)

    asyncio.run(sandbox.start(settings(pool_size=2)))
    try:
        for _ in range(4):
            asyncio.run(one())
        assert len(sandbox._live) <= 2 * 2, "at most the current and one earlier loop's children are alive"
    finally:
        asyncio.run(sandbox.stop())
    assert not sandbox._live
