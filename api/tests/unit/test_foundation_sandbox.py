"""The parser sandbox (audit 13, DP-09): PDF and image parsing runs in recycled child processes that can be killed.

Every test runs real child processes (`spawn`) with synthetic inputs. The canary strings stand for patient text:
no error, repr or log line may carry them (rule 6).
"""

from __future__ import annotations

import asyncio
import io
import logging
import multiprocessing
import os
import sys
import time
from collections.abc import AsyncIterator

import pikepdf
import pytest

from app.core import sandbox
from app.core.config import SandboxSettings
from app.core.sandbox import SandboxError, run
from tests.support import sandbox_fns as fns

pytestmark = pytest.mark.anyio

CANARY = "CANARY-patient-Jane-Doe-0123456789"
LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="RLIMIT_AS is enforced on Linux (production); macOS does not honour it, so the memory kill is not testable here",
)


def settings(**kw: object) -> SandboxSettings:
    base = {"pool_size": 1, "timeout_seconds": 20.0, "memory_bytes": 512 * 1024 * 1024, "recycle_after": 50}
    return SandboxSettings(**{**base, **kw})  # type: ignore[arg-type]


@pytest.fixture
async def pool() -> AsyncIterator[None]:
    await sandbox.start(settings())
    yield
    await sandbox.stop()


async def test_a_call_runs_in_a_child_and_returns_its_result(pool: None) -> None:
    assert await run(fns.add, 2, 3) == 5
    assert await run(fns.echo, b"\x00\x01bytes") == b"\x00\x01bytes"
    assert await run(fns.pid) != os.getpid()


async def test_a_call_that_sleeps_past_the_timeout_is_killed_and_the_next_call_succeeds(pool: None) -> None:
    started = time.monotonic()
    with pytest.raises(SandboxError) as caught:
        await run(fns.sleep_for, 60, timeout=0.5)
    assert caught.value.code == "timed_out"
    assert time.monotonic() - started < 10
    assert await run(fns.add, 1, 1) == 2


async def test_a_crashing_child_is_replaced(pool: None) -> None:
    with pytest.raises(SandboxError) as caught:
        await run(fns.hard_exit, 7)
    assert caught.value.code == "crashed"
    assert await run(fns.add, 4, 5) == 9


@LINUX_ONLY
async def test_a_call_that_allocates_past_the_limit_is_stopped_and_the_next_call_succeeds() -> None:
    await sandbox.start(settings(memory_bytes=256 * 1024 * 1024))
    try:
        with pytest.raises(SandboxError) as caught:
            await run(fns.allocate, 2 * 1024 * 1024 * 1024)
        assert caught.value.code == "out_of_memory"
        assert await run(fns.add, 1, 2) == 3
    finally:
        await sandbox.stop()


async def test_calls_beyond_the_pool_size_queue_rather_than_fail() -> None:
    await sandbox.start(settings(pool_size=2))
    try:
        started = time.monotonic()
        pids = await asyncio.gather(*(run(fns.sleep_for, 0.6) for _ in range(4)))
        assert len(set(pids)) <= 2, "four calls shared two children"
        assert time.monotonic() - started >= 1.1, "the third and fourth waited for a free child"
    finally:
        await sandbox.stop()


async def test_a_child_is_recycled_after_the_configured_number_of_tasks() -> None:
    await sandbox.start(settings(recycle_after=2))
    try:
        pids = [await run(fns.pid) for _ in range(5)]
    finally:
        await sandbox.stop()
    assert pids[0] == pids[1] != pids[2] == pids[3] != pids[4]


async def test_an_exception_from_the_function_arrives_as_its_own_type(pool: None) -> None:
    with pytest.raises(ValueError) as caught:
        await run(fns.raise_value, "plain failure")
    assert caught.value.args == ("plain failure",)
    assert await run(fns.add, 1, 1) == 2, "the child survives a normal exception"


async def test_a_pikepdf_password_error_keeps_its_type(pool: None) -> None:
    buffer = io.BytesIO()
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(buffer, encryption=pikepdf.Encryption(user="u", owner="o"))
    with pytest.raises(pikepdf.PasswordError):
        await run(fns.open_encrypted_pdf, buffer.getvalue())


async def test_an_exception_that_cannot_cross_the_pipe_becomes_a_code_with_no_text(pool: None) -> None:
    for call in (fns.raise_unpicklable, fns.unpicklable_result):
        with pytest.raises(SandboxError) as caught:
            await run(call, CANARY)
        assert caught.value.code == "failed"
        assert CANARY not in str(caught.value) + repr(caught.value) + repr(caught.value.args)
        assert caught.value.__cause__ is None and caught.value.__context__ is None or CANARY not in repr(
            caught.value.__context__
        )


async def test_no_argument_text_reaches_logs_output_or_errors(
    pool: None, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    errors: list[BaseException] = []
    for call, kw in ((fns.chatter_then_fail, {}), (fns.sleep_for, {"timeout": 0.3}), (fns.hard_exit, {})):
        args = (CANARY,) if call is fns.chatter_then_fail else ((60,) if call is fns.sleep_for else (7,))
        with pytest.raises(SandboxError) as caught:
            await run(call, *args, **kw)  # type: ignore[arg-type]
        errors.append(caught.value)
    # a bytes argument that is itself the canary
    with pytest.raises(SandboxError):
        await run(fns.sleep_for, 60, timeout=0.3)
    out = capfd.readouterr()
    everything = caplog.text + out.out + out.err + "".join(repr(e) + str(e) for e in errors)
    assert CANARY not in everything


async def test_stop_leaves_no_child_processes() -> None:
    await sandbox.start(settings(pool_size=2))
    pids = set(await asyncio.gather(run(fns.sleep_for, 0.3), run(fns.sleep_for, 0.3)))
    await sandbox.stop()
    assert multiprocessing.active_children() == []
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


async def test_stop_while_a_call_is_running_ends_it_and_leaves_no_child() -> None:
    await sandbox.start(settings())
    task = asyncio.ensure_future(run(fns.sleep_for, 60))
    await asyncio.sleep(1.0)
    await sandbox.stop()
    with pytest.raises(SandboxError):
        await task
    assert multiprocessing.active_children() == []


async def test_a_cancelled_call_kills_its_child_and_frees_the_slot(pool: None) -> None:
    task = asyncio.ensure_future(run(fns.sleep_for, 60))
    await asyncio.sleep(0.8)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await run(fns.add, 1, 1) == 2


async def test_run_starts_the_pool_by_itself_when_nobody_did() -> None:
    try:
        assert await run(fns.add, 1, 2) == 3
    finally:
        await sandbox.stop()


def test_the_error_carries_only_a_code() -> None:
    error = SandboxError("timed_out")
    assert error.code == "timed_out" and str(error) == "timed_out"
