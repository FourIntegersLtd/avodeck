"""Audit 13 review of the sandbox: a cancelled call and the next caller (F-a), a stale or foreign reply and a closed
connection under a running thread (F-b), one pickle per reply and files by path (F-d), a child that outlives its
parent and a CPU limit (F-f). Real child processes, synthetic inputs."""

from __future__ import annotations

import asyncio
import os
import pickle
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import pytest

from app.core import case_files, sandbox
from app.core.config import SandboxSettings
from app.core.sandbox import SandboxError, run
from tests.support import sandbox_fns as fns

SLOW = "tests.support.slow_import"
API = Path(__file__).resolve().parents[2]


def settings(**kw: Any) -> SandboxSettings:
    base: dict[str, Any] = {
        "pool_size": 1,
        "timeout_seconds": 20.0,
        "memory_bytes": 512 * 1024 * 1024,
        "recycle_after": 50,
    }
    return SandboxSettings(**{**base, **kw})


@pytest.fixture
async def pool() -> AsyncIterator[None]:
    await sandbox.start(settings())
    yield
    await sandbox.stop()


# --- F-a ----------------------------------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("attempt", [1, 2, 3])
async def test_a_call_cancelled_while_it_waits_for_a_new_child_does_not_cost_the_next_caller_its_child(
    attempt: int,
) -> None:
    await sandbox.start(settings(), prewarm=[SLOW])  # the one child takes about 1.5 s to start
    try:
        waiting = asyncio.ensure_future(run(fns.add, 1, 1))
        await asyncio.sleep(0.3)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert await run(fns.add, 2, 3) == 5, "the next caller got the child, not an error"
    finally:
        await sandbox.stop()


@pytest.mark.anyio
async def test_the_slot_of_a_cancelled_call_is_not_handed_out_until_its_thread_has_finished() -> None:
    await sandbox.start(settings(), prewarm=[SLOW])
    try:
        waiting = asyncio.ensure_future(run(fns.add, 1, 1))
        await asyncio.sleep(0.3)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        pool = sandbox._pool
        assert pool is not None
        (mine,) = pool.loops.values()
        assert mine.idle.qsize() == 0, "the slot is still held by the thread that waits for the starting child"
        await asyncio.sleep(4)
        assert mine.idle.qsize() == 1, "and is back once that thread has finished"
    finally:
        await sandbox.stop()


# --- F-b ----------------------------------------------------------------------------------------------------


def _fake_child() -> tuple[sandbox._Child, Connection, subprocess.Popen[bytes]]:
    mine, theirs = socket.socketpair()
    process = subprocess.Popen(["sleep", "30"])  # noqa: S603, S607 - a stand-in process that stays alive
    return sandbox._Child(process, Connection(mine.detach())), Connection(theirs.detach()), process


def _answer_with(conn: Connection, tag: bytes | None, value: int) -> threading.Thread:
    def play() -> None:
        message = conn.recv()
        nonce = message[0]
        conn.send_bytes(pickle.dumps(((tag + nonce) if tag else nonce, "ok", value)))

    thread = threading.Thread(target=play, daemon=True)
    thread.start()
    return thread


def test_a_reply_that_carries_the_calls_own_nonce_is_accepted() -> None:
    child, theirs, process = _fake_child()
    slot = sandbox._Slot(settings(), ())
    slot.child = child
    try:
        _answer_with(theirs, None, 3)
        assert sandbox._call(slot, fns.add, (1, 2), 5.0, threading.Event()) == 3
    finally:
        slot.close()
        process.kill()
        process.wait()


def test_a_reply_with_another_nonce_is_refused_and_the_child_is_discarded() -> None:
    child, theirs, process = _fake_child()
    slot = sandbox._Slot(settings(), ())
    slot.child = child
    try:
        _answer_with(theirs, b"other-", 3)
        with pytest.raises(SandboxError) as caught:
            sandbox._call(slot, fns.add, (1, 2), 5.0, threading.Event())
        assert caught.value.code == "crashed"
        assert slot.child is not child, "the child that answered out of turn is not used again"
        assert process.wait(5) is not None
    finally:
        slot.close()
        process.kill()


def test_a_connection_in_use_is_not_closed_by_another_thread_but_by_its_owner() -> None:
    child, theirs, process = _fake_child()
    try:
        child.begin_io()
        child.retire()  # from another thread's point of view: kill the process, leave the connection alone
        assert not child.conn.closed, "the thread inside send or recv still owns the connection"
        child.end_io()
        assert child.conn.closed, "the owner closes it when it is done"
    finally:
        process.kill()
        process.wait()


def test_a_connection_not_in_use_is_closed_by_retire() -> None:
    child, theirs, process = _fake_child()
    try:
        child.retire()
        assert child.conn.closed
        with pytest.raises(SandboxError) as caught:
            child.begin_io()
        assert caught.value.code == "unavailable"
    finally:
        process.kill()
        process.wait()


# --- F-d ----------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_result_is_pickled_once_in_the_child(pool: None) -> None:
    assert await run(fns.pickled_times) == 1


@pytest.mark.anyio
async def test_a_file_in_the_scratch_directory_can_be_read_by_path(pool: None) -> None:
    payload = os.urandom(300_000)
    with case_files.scratch_file(payload, suffix=".pdf") as path:
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
        assert await run(fns.file_size, path) == len(payload)


# --- F-f ----------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_child_has_a_cpu_limit_derived_from_the_calls_timeout(pool: None) -> None:
    limit, used = await run(fns.cpu_limit, timeout=5)
    assert 25 <= limit - used <= 28, (limit, used)  # four cores' worth of the timeout, and a small margin


def test_a_child_does_not_outlive_a_parent_that_was_killed() -> None:
    parent = subprocess.Popen(  # noqa: S603 - our own module
        [sys.executable, "-m", "tests.support.sandbox_orphan_parent"],
        cwd=API,
        stdout=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(API)},
    )
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline())
        time.sleep(1.0)  # the child is inside its long call
        parent.kill()
        parent.wait()
        deadline = time.monotonic() + 10
        gone = False
        while time.monotonic() < deadline and not gone:
            try:
                os.kill(child_pid, 0)
                time.sleep(0.2)
            except ProcessLookupError:
                gone = True
        if not gone:
            os.kill(child_pid, 9)
        assert gone, "the child went on after its parent was killed"
    finally:
        parent.kill()
