"""Audit 13 review: which `SandboxError` codes blame the input and which do not.

`timed_out`, `out_of_memory`, `crashed` (the child died while it had the call) and `failed` mean "this input". The
sandbox being unable to serve the call at all (a child that would not start, or was already gone when the call was
sent) is `unavailable`, and a stopped pool is `stopped`: neither is the file's fault, so a caller may retry the job
instead of marking the file.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.core import sandbox
from app.core.config import SandboxSettings
from app.core.sandbox import SandboxError
from tests.support import sandbox_fns as fns

pytestmark = pytest.mark.anyio

CFG = SandboxSettings(pool_size=1, timeout_seconds=10.0, memory_bytes=512 * 1024 * 1024, recycle_after=50)
NOT_THE_INPUTS_FAULT = {"unavailable", "stopped"}


@pytest.fixture(autouse=True)
async def clean() -> AsyncIterator[None]:
    yield
    await sandbox.stop()


async def test_a_child_that_cannot_be_started_is_unavailable_not_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> Any:
        raise OSError("no more processes")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    await sandbox.start(CFG)
    with pytest.raises(SandboxError) as caught:
        await sandbox.run(fns.add, 1, 2)
    assert caught.value.code == "unavailable"


async def test_a_child_that_exits_before_it_is_ready_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "executable", "/bin/false")
    await sandbox.start(CFG)
    with pytest.raises(SandboxError) as caught:
        await sandbox.run(fns.add, 1, 2)
    assert caught.value.code == "unavailable"


async def test_a_child_that_dies_while_it_has_the_call_is_still_crashed() -> None:
    await sandbox.start(CFG)
    with pytest.raises(SandboxError) as caught:
        await sandbox.run(fns.hard_exit, 7)
    assert caught.value.code == "crashed", "control: a death during the call is the input's"
    assert caught.value.code not in NOT_THE_INPUTS_FAULT


async def test_a_stopped_pool_is_stopped() -> None:
    await sandbox.start(CFG)
    assert await sandbox.run(fns.add, 1, 2) == 3
    pool = sandbox._pool
    assert pool is not None
    await pool.stop()
    with pytest.raises(SandboxError) as caught:
        await pool.run(fns.add, (1, 2), None)
    assert caught.value.code == "stopped"
    sandbox._pool = None
