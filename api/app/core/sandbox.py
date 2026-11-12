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
`recycle_after` tasks, and on a timeout, a cancelled call, or any death. The replacement is started and warmed in
the background before the old child is given up, so no call waits for a child to start; `start(prewarm=[...])` names
modules every child imports as it starts (the worker passes the pipeline's parsing module: core never imports a
feature). `start` and `stop` are called by the worker; `run` starts a pool with the environment's settings if nobody
did. Each running event loop has its own slots.

The address-space limit is enforced on Linux, the production platform, and is a no-op on macOS. A child exits
when its parent does (the socket closes), and the parent kills any left at exit.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
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
class _Child:
    """One running child process and the socket to it."""

    process: subprocess.Popen[bytes]
    conn: Connection
    tasks: int = 0

    def alive(self) -> bool:
        return self.process.poll() is None

    def kill(self) -> None:
        """End the child now (blocking, short) and forget it."""
        try:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(_STOP_GRACE_SECONDS)
        except (OSError, subprocess.SubprocessError):
            pass
        _live.discard(self.process)
        try:
            self.conn.close()
        except OSError:
            pass


def _start_child(
    memory_bytes: int, prewarm: tuple[str, ...], seen: Callable[[subprocess.Popen[bytes]], None]
) -> _Child:
    """Start a child and wait until it is ready (modules imported). Blocking: call it in a thread."""
    mine, theirs = socket.socketpair()
    try:
        process = subprocess.Popen(  # noqa: S603 - our own interpreter and module, no shell
            [sys.executable, "-m", "app.core.sandbox_child", str(theirs.fileno()), str(memory_bytes), *prewarm],
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
    child = _Child(process, Connection(mine.detach()))
    _live.add(process)
    seen(process)
    try:
        if not child.conn.poll(STARTUP_SECONDS) or child.conn.recv() != "ready":
            raise SandboxError("crashed")
    except (EOFError, OSError):
        child.kill()
        raise SandboxError("crashed") from None
    except SandboxError:
        child.kill()
        raise
    return child


class _Warm:
    """A child being started in a background thread, so that nobody has to wait for it when it is needed."""

    def __init__(self, memory_bytes: int, prewarm: tuple[str, ...]) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._process: subprocess.Popen[bytes] | None = None
        self._child: _Child | None = None
        self.error: SandboxError | None = None
        self.done = threading.Event()
        threading.Thread(target=self._run, args=(memory_bytes, prewarm), daemon=True, name="sandbox-warm").start()

    def _seen(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._process = process
            if self._closed:
                process.kill()

    def _run(self, memory_bytes: int, prewarm: tuple[str, ...]) -> None:
        try:
            child = _start_child(memory_bytes, prewarm, self._seen)
        except SandboxError as exc:
            self.error = exc
        else:
            with self._lock:
                if not self._closed:
                    self._child = child
                    child = None  # type: ignore[assignment]
            if child is not None:
                child.kill()
        finally:
            self.done.set()

    def take(self, timeout: float | None = None) -> _Child:
        """The ready child (waiting for it if need be); `SandboxError("crashed")` if it could not start."""
        self.done.wait(timeout)
        with self._lock:
            child, self._child = self._child, None
        if child is None:
            raise self.error or SandboxError("crashed")
        return child

    def cancel(self) -> None:
        """Do not wait for it: the child, started or not, is killed."""
        with self._lock:
            self._closed = True
            process, child, self._child = self._process, self._child, None
        if child is not None:
            child.kill()
        elif process is not None:
            try:
                process.kill()
            except OSError:
                pass


class _Slot:
    """One place in the pool: a child, and the next child already starting. A child is replaced after
    `recycle_after` tasks, or when it dies; the replacement is started in the background BEFORE the old one is given
    up, so a call never waits for a start (a start costs about 2 s once the parsing modules are imported)."""

    def __init__(self, cfg: SandboxSettings, prewarm: tuple[str, ...]) -> None:
        self.cfg, self.prewarm = cfg, prewarm
        self.lock = threading.Lock()
        self.child: _Child | None = None
        self.warm: _Warm | None = None
        self.closed = False

    def _begin_warm(self) -> None:  # lock held
        if self.warm is None and not self.closed:
            self.warm = _Warm(self.cfg.memory_bytes, self.prewarm)

    def prepare(self) -> None:
        with self.lock:
            if self.child is None:
                self._begin_warm()

    def acquire(self) -> _Child:
        """The child for the next call: the current one, or (when it is due or gone) the warmed replacement."""
        with self.lock:
            if self.closed:
                raise SandboxError("stopped")
            child = self.child
            if child is not None and not child.alive():
                child.kill()
                child = self.child = None
            due = child is not None and child.tasks >= self.cfg.recycle_after
            if child is None or due:
                self._begin_warm()
            warm = self.warm
            if child is not None:
                if due and warm is not None and warm.done.is_set():
                    self.warm = None
                    try:
                        replacement = warm.take()
                    except SandboxError:
                        return child  # the replacement did not start; keep the old child, try again next time
                    child.kill()
                    self.child = replacement
                    return replacement
                return child  # due but the replacement is still starting: the old child goes on serving
        assert warm is not None
        try:
            fresh = warm.take()
        except SandboxError:
            with self.lock:
                if self.warm is warm:
                    self.warm = None
                closed = self.closed
            raise SandboxError("stopped" if closed else "crashed") from None
        with self.lock:
            if self.warm is warm:
                self.warm = None
            if self.closed:
                fresh.kill()
                raise SandboxError("stopped")
            self.child = fresh
        return fresh

    def finished(self, child: _Child) -> None:
        """A call ended well: if the child is due, start its replacement now, while nobody is waiting."""
        with self.lock:
            if self.child is child and child.tasks >= self.cfg.recycle_after:
                self._begin_warm()

    def discard(self, child: _Child | None) -> None:
        """The child timed out, died, leaked or was cancelled: end it, and start another in the background."""
        if child is None:
            return
        with self.lock:
            if self.child is child:
                self.child = None
                self._begin_warm()
        child.kill()

    def discard_current(self) -> None:
        with self.lock:
            child = self.child
        self.discard(child)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            child, self.child = self.child, None
            warm, self.warm = self.warm, None
        if child is not None:
            child.kill()
        if warm is not None:
            warm.cancel()


def _call(
    slot: _Slot, fn: Callable[..., Any], args: tuple[Any, ...], timeout: float, cancelled: threading.Event
) -> Any:
    """One call in a worker thread: take the child, send, wait with a deadline, read the reply."""
    child = slot.acquire()
    if cancelled.is_set():  # the caller gave up while this waited for a child
        return None
    conn = child.conn
    try:
        conn.send((fn, args))
    except (OSError, ValueError):
        slot.discard(child)
        raise SandboxError("crashed") from None
    except BaseException:  # noqa: BLE001 - arguments that cannot be pickled; their text stays out of the error
        slot.discard(child)
        raise SandboxError("failed") from None
    child.tasks += 1
    try:
        if not conn.poll(timeout):
            logger.warning("sandboxed call timed out after %.1f s", timeout)
            slot.discard(child)
            raise SandboxError("timed_out")
        reply = conn.recv()
    except (EOFError, OSError):
        code = "stopped" if slot.closed else _death_code(child)
        slot.discard(child)
        logger.warning("sandboxed call ended: %s", code)
        raise SandboxError(code) from None
    kind, value = reply
    if kind == "ok":
        slot.finished(child)
        return value
    if kind == "raised":
        slot.finished(child)
        raise value
    slot.discard(child)  # a child that ran out of memory is not trusted again
    raise SandboxError("out_of_memory" if kind == "memory" else "failed")


def _death_code(child: _Child) -> str:
    try:
        code = child.process.wait(1.0)
    except subprocess.SubprocessError:
        return "crashed"
    return "out_of_memory" if code == -signal.SIGKILL else "crashed"


class _LoopSlots:
    """The slots one event loop uses. An `asyncio.Queue` belongs to the loop it first waits in, so each running loop
    gets its own slots (and children); in the worker there is one loop and so one set."""

    def __init__(self, cfg: SandboxSettings, prewarm: tuple[str, ...]) -> None:
        self.slots = [_Slot(cfg, prewarm) for _ in range(cfg.pool_size)]
        self.idle: asyncio.Queue[_Slot] = asyncio.Queue()
        for slot in self.slots:
            self.idle.put_nowait(slot)
            if prewarm:  # the worker names modules: its children start now, not at the first page
                slot.prepare()

    def close(self) -> None:
        for slot in self.slots:
            slot.close()


class _Pool:
    def __init__(self, cfg: SandboxSettings, prewarm: tuple[str, ...]) -> None:
        self.cfg, self.prewarm = cfg, prewarm
        self.loops: dict[asyncio.AbstractEventLoop, _LoopSlots] = {}
        self.stopped = False

    def _slots(self) -> _LoopSlots:
        loop = asyncio.get_running_loop()
        mine = self.loops.get(loop)
        if mine is None:
            for gone in [other for other in self.loops if other.is_closed()]:  # children of finished loops end
                self.loops.pop(gone).close()
            mine = self.loops[loop] = _LoopSlots(self.cfg, self.prewarm)
        return mine

    async def run(self, fn: Callable[..., Any], args: tuple[Any, ...], timeout: float | None) -> Any:
        mine = self._slots()
        slot = await mine.idle.get()
        cancelled = threading.Event()
        try:
            if self.stopped:
                raise SandboxError("stopped")
            return await asyncio.to_thread(_call, slot, fn, args, timeout or self.cfg.timeout_seconds, cancelled)
        except asyncio.CancelledError:
            cancelled.set()
            slot.discard_current()  # frees the thread that is waiting on the pipe
            raise
        finally:
            mine.idle.put_nowait(slot)

    async def stop(self) -> None:
        self.stopped = True
        everything = [slot for mine in self.loops.values() for slot in mine.slots]
        await asyncio.gather(*(asyncio.to_thread(slot.close) for slot in everything))


_pool: _Pool | None = None
_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


async def start(cfg: SandboxSettings | None = None, *, prewarm: Sequence[str] = ()) -> None:
    """Make the pool. A second `start` replaces the first, which is stopped.

    `prewarm` names modules (dotted names) each child imports as it starts, before it is ready, so the first call
    does not pay for them (the worker names the pipeline's parsing module). With it, the children start at once, in
    the background; without it they start on first use. A module that cannot be imported is skipped."""
    global _pool
    names = tuple(prewarm)
    for name in names:
        if not _MODULE_NAME.fullmatch(name):
            raise ValueError("prewarm names must be dotted module names")
    if _pool is not None:
        await stop()
    _pool = _Pool(cfg or sandbox_settings(), names)
    if names:
        _pool._slots()  # starts the children for this loop now


async def stop() -> None:
    """Kill every child, including ones mid-call (those calls get `SandboxError("stopped")`) and ones still starting,
    and drop the pool."""
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
