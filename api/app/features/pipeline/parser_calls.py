"""How the pipeline treats a sandboxed parser call that did not complete (audit 13, DP-09 review).

Only a failure that is the input's doing may mark the input: `timed_out`, `crashed`, `out_of_memory` and `failed`.
`unavailable` (no child could serve the call) and `stopped` (the pool was stopping) say nothing about the file, so
they are raised again and the job is retried. A `timed_out` or `crashed` call gets one more try in a fresh child
before the caller marks anything. Codes only: nothing here sees or says what a file holds.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core import sandbox

INPUT_FAULTS = frozenset({"timed_out", "crashed", "out_of_memory", "failed"})
RETRIED = frozenset({"timed_out", "crashed"})

_FREE_MIB = 32  # a file this small gets the cell's timeout as it is
_SECONDS_PER_MIB = 0.5
_MAX_FACTOR = 4  # the most a big file may wait, as a multiple of the cell's timeout


def is_input_fault(error: sandbox.SandboxError) -> bool:
    """Whether the failure was this input's doing. If not, the caller re-raises so the job retries."""
    return error.code in INPUT_FAULTS


def count_timeout(size_bytes: int, base: float) -> float:
    """Seconds a page count may take: the cell's `base` for a small file, more as the file grows, never more than
    four times `base`."""
    extra = max(0.0, size_bytes / (1024 * 1024) - _FREE_MIB) * _SECONDS_PER_MIB
    return min(_MAX_FACTOR * base, base + extra)


async def run[T](fn: Callable[..., T], *args: Any, timeout: float | None = None) -> T:
    """`sandbox.run`, with one more try in a fresh child after a `timed_out` or `crashed` call."""
    try:
        return await sandbox.run(fn, *args, timeout=timeout)
    except sandbox.SandboxError as error:
        if error.code not in RETRIED:
            raise
    return await sandbox.run(fn, *args, timeout=timeout)
