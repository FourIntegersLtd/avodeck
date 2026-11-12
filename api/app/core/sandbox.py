"""Run risky parsing (PDF, image) in killable child processes (audit 13, DP-09).

pikepdf, pypdfium2 and Pillow are native code fed with files a stranger made. In a worker thread a page that
hangs or balloons cannot be stopped and takes the whole worker with it. Here each call runs in a child of a small
pool (fresh `python -m app.core.sandbox_child` processes, so a child shares nothing with the worker), with a
wall-clock timeout and an address-space limit; a child that overruns, dies or leaks is killed and replaced,
and the worker carries on.

    from app.core.sandbox import run, SandboxError

    pages = await run(count_pages, pdf_bytes)            # count_pages is a top-level function, bytes in, int out
    try: ...
    except SandboxError as exc: exc.code                 # timed_out, out_of_memory, crashed, failed, stopped

`fn` must be an importable top-level function; its arguments and result must be picklable. An exception `fn`
raises comes back as its own type when it can be pickled (so `pikepdf.PasswordError` still arrives as such); one
that cannot becomes `SandboxError("failed")`. A `SandboxError` carries a code and nothing else: never the child's
exception text or the arguments (rule 6). A child gets only the socket to its parent (every other descriptor is
closed), an allow-listed environment (no keys or database URLs), stdout and stderr on /dev/null, logging disabled
and core dumps off. A child is replaced after
`recycle_after` tasks, and on a timeout, a cancelled call, or any death. `start` and `stop` are called by the
worker; `run` starts a pool with the environment's settings if nobody did.

The address-space limit is enforced on Linux, the production platform, and is a no-op on macOS. A child exits
when its parent does (the socket closes), and the parent kills any left at exit.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

from app.core.config import SandboxSettings, sandbox_settings

logger = logging.getLogger("app.sandbox")

STARTUP_SECONDS = 60.0
_STOP_GRACE_SECONDS = 2.0


class SandboxError(Exception):
    """A sandboxed call did not complete. `code` is `timed_out`, `out_of_memory`, `crashed`, `failed` or
    `stopped`; there is no other text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __reduce__(self) -> tuple[type[SandboxError], tuple[str]]:
        return (SandboxError, (self.code,))


# --- the pool --------------------------------------------------------------------------------------------

_ENV_KEPT = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "VIRTUAL_ENV", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")
_live: set[subprocess.Popen[bytes]] = set()


def _child_env() -> dict[str, str]:
    """A child sees no secrets: a short allow-list of the worker's variables, and its import path."""
    env = {name: os.environ[name] for name in _ENV_KEPT if name in os.environ}
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@atexit.register
def _kill_leftovers() -> None:
    for process in list(_live):
        try:
            process.kill()
            process.wait(_STOP_GRACE_SECONDS)
        except (OSError, subprocess.SubprocessError):
            pass


@dataclass
class _Slot:
    process: subprocess.Popen[bytes] | None = None
    conn: Connection | None = None
    tasks: int = 0

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def kill(self) -> None:
        """End the child now (blocking, short) and forget it."""
        process, conn = self.process, self.conn
        self.process, self.conn, self.tasks = None, None, 0
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(_STOP_GRACE_SECONDS)
            except (OSError, subprocess.SubprocessError):
                pass
            _live.discard(process)
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    def retire(self) -> None:
        """Ask the child to finish, then make sure it has."""
        process, conn = self.process, self.conn
        if conn is not None:
            try:
                conn.send(None)
            except (OSError, ValueError):
                pass
        if process is not None:
            try:
                process.wait(_STOP_GRACE_SECONDS)
            except (OSError, subprocess.SubprocessError):
                pass
        self.kill()

    def spawn(self, memory_bytes: int) -> None:
        mine, theirs = socket.socketpair()
        try:
            process = subprocess.Popen(  # noqa: S603 - our own interpreter and module, no shell
                [sys.executable, "-m", "app.core.sandbox_child", str(theirs.fileno()), str(memory_bytes)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=[theirs.fileno()],
                close_fds=True,
                env=_child_env(),
                start_new_session=True,  # a Ctrl-C meant for the worker does not reach the children
            )
        except OSError:
            mine.close()
            theirs.close()
            raise SandboxError("crashed") from None
        theirs.close()
        self.process, self.conn, self.tasks = process, Connection(mine.detach()), 0
        _live.add(process)
        try:
            assert self.conn is not None
            if not self.conn.poll(STARTUP_SECONDS) or self.conn.recv() != "ready":
                raise SandboxError("crashed")
        except (EOFError, OSError):
            self.kill()
            raise SandboxError("crashed") from None
        except SandboxError:
            self.kill()
            raise


def _call(slot: _Slot, cfg: SandboxSettings, fn: Callable[..., Any], args: tuple[Any, ...], timeout: float) -> Any:
    """One call in a worker thread: ready the child, send, wait with a deadline, read the reply."""
    if slot.alive() and slot.tasks >= cfg.recycle_after:
        slot.retire()
    if not slot.alive():
        slot.kill()
        slot.spawn(cfg.memory_bytes)
    conn = slot.conn
    assert conn is not None and slot.process is not None
    try:
        conn.send((fn, args))
    except (OSError, ValueError):
        slot.kill()
        raise SandboxError("crashed") from None
    except BaseException:  # noqa: BLE001 - arguments that cannot be pickled; their text stays out of the error
        slot.kill()
        raise SandboxError("failed") from None
    slot.tasks += 1
    try:
        if not conn.poll(timeout):
            logger.warning("sandboxed call timed out after %.1f s", timeout)
            slot.kill()
            raise SandboxError("timed_out")
        reply = conn.recv()
    except (EOFError, OSError):
        code = _death_code(slot)
        slot.kill()
        logger.warning("sandboxed call ended: %s", code)
        raise SandboxError(code) from None
    kind, value = reply
    if kind == "ok":
        return value
    if kind == "raised":
        raise value
    slot.kill()  # a child that ran out of memory is not trusted again
    raise SandboxError("out_of_memory" if kind == "memory" else "failed")


def _death_code(slot: _Slot) -> str:
    process = slot.process
    if process is None:
        return "crashed"
    try:
        code = process.wait(1.0)
    except subprocess.SubprocessError:
        return "crashed"
    return "out_of_memory" if code == -signal.SIGKILL else "crashed"


class _Pool:
    def __init__(self, cfg: SandboxSettings) -> None:
        self.cfg = cfg
        self.slots = [_Slot() for _ in range(cfg.pool_size)]
        self.idle: asyncio.Queue[_Slot] = asyncio.Queue()
        for slot in self.slots:
            self.idle.put_nowait(slot)
        self.stopped = False

    async def run(self, fn: Callable[..., Any], args: tuple[Any, ...], timeout: float | None) -> Any:
        slot = await self.idle.get()
        try:
            if self.stopped:
                raise SandboxError("stopped")
            return await asyncio.to_thread(_call, slot, self.cfg, fn, args, timeout or self.cfg.timeout_seconds)
        except asyncio.CancelledError:
            slot.kill()  # frees the thread that is waiting on the pipe
            raise
        finally:
            self.idle.put_nowait(slot)

    async def stop(self) -> None:
        self.stopped = True
        await asyncio.gather(*(asyncio.to_thread(slot.kill) for slot in self.slots))


_pool: _Pool | None = None


async def start(cfg: SandboxSettings | None = None) -> None:
    """Make the pool (children start on first use). A second `start` replaces the first, which is stopped."""
    global _pool
    if _pool is not None:
        await stop()
    _pool = _Pool(cfg or sandbox_settings())


async def stop() -> None:
    """Kill every child, including ones mid-call (those calls get `SandboxError("stopped")`), and drop the pool."""
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.stop()


async def run[T](fn: Callable[..., T], *args: Any, timeout: float | None = None) -> T:
    """Call `fn(*args)` in a pooled child; wait for a free one when all are busy. `timeout` (seconds) overrides
    the setting for this call. See the module docstring for the errors."""
    if _pool is None:
        await start()
    assert _pool is not None
    return await _pool.run(fn, args, timeout)  # type: ignore[no-any-return]


__all__ = ["SandboxError", "run", "start", "stop"]
