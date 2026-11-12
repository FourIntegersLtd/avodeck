"""The child side of `app.core.sandbox`: `python -m app.core.sandbox_child <fd> <memory_bytes> [module ...]`.

Imports only the standard library, so a child starts fast and carries nothing of the worker (no settings, no
Sentry, no log handlers). It reads `(fn, args)` from the socket the parent passed, calls `fn(*args)` and sends back
`(nonce, "ok", result)`, `(nonce, "raised", exception)`, `(nonce, "memory", None)` or `(nonce, "failed", None)`, pickled
once and sent as bytes. A request is `(nonce, cpu_seconds, fn, args)`; the nonce comes back so the parent can tell an
answer to this call from anything else. It never writes to stdout or
stderr and never logs, so nothing it was given can leave through them (rule 6). Modules named after the memory limit
are imported before it says "ready" (the parent's `prewarm`), so the import cost is paid at start, not by a call; one
that fails to import is skipped and shows up again, as it would, when a call needs it.
"""

from __future__ import annotations

import importlib
import logging
import os
import pickle
import sys
import threading
import time
from multiprocessing.connection import Connection
from typing import Any


def _lock_down(memory_bytes: int) -> None:
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (1, 2):
        os.dup2(devnull, fd)
    os.close(devnull)
    logging.disable(logging.CRITICAL)
    for handler in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(handler)
    sys.excepthook = lambda *_args: None
    import resource

    for name, value in (("RLIMIT_CORE", 0), ("RLIMIT_AS", memory_bytes)):  # no core dump of a page; a memory ceiling
        try:
            resource.setrlimit(getattr(resource, name), (value, value))
        except (
            AttributeError,
            ValueError,
            OSError,
        ):  # macOS does not honour RLIMIT_AS; the parent's timeout still applies
            pass


def _cpu_limit(seconds: int) -> None:
    """This call may use `seconds` more CPU time (the parent's timeout, times a few cores, and a margin). The parent
    kills a child that overruns the wall clock; this limit ends one the parent can no longer reach (it died while the
    child held the GIL in native code). SIGXCPU ends the process."""
    try:
        import resource

        used = sum(os.times()[:2])
        hard = resource.getrlimit(resource.RLIMIT_CPU)[1]
        resource.setrlimit(resource.RLIMIT_CPU, (int(used) + seconds, hard))
    except (AttributeError, ValueError, OSError):
        pass


def _watch_parent(parent: int) -> None:
    """Leave when the parent does. The socket closing ends an idle child; a child in the middle of a call, in code
    that never reads the socket, is ended here. (`PR_SET_PDEATHSIG` is not used: on Linux it fires when the parent
    *thread* that started the child exits, and children are started from short-lived threads.)"""
    while True:
        time.sleep(1.0)
        if os.getppid() != parent:
            os._exit(1)


def _reply(nonce: bytes, fn: Any, args: tuple[Any, ...]) -> bytes:
    """The pickled answer, made once and sent as bytes (not pickled a second time by `send`)."""
    try:
        result = fn(*args)
        try:
            return pickle.dumps((nonce, "ok", result))
        except BaseException:  # noqa: BLE001 - a result that cannot cross the pipe; say nothing about it
            return pickle.dumps((nonce, "failed", None))
    except MemoryError:
        return pickle.dumps((nonce, "memory", None))
    except BaseException as exc:  # noqa: BLE001 - everything goes back to the parent as data
        try:
            payload = pickle.dumps((nonce, "raised", exc))
            pickle.loads(payload)  # an exception the parent could not rebuild is not sent
        except BaseException:  # noqa: BLE001 - this exception cannot cross the pipe
            return pickle.dumps((nonce, "failed", None))
        return payload


def main(fd: int, memory_bytes: int, modules: list[str] | None = None) -> None:
    conn = Connection(fd)
    _lock_down(memory_bytes)
    threading.Thread(target=_watch_parent, args=(os.getppid(),), daemon=True).start()
    for name in modules or ():
        try:
            importlib.import_module(name)
        except BaseException:  # noqa: BLE001 - a module that will not import is the call's problem, not the start's
            pass
    conn.send("ready")
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):  # the parent is gone
            return
        if message is None:
            return
        nonce, cpu_seconds, fn, args = message
        _cpu_limit(cpu_seconds)
        reply = _reply(nonce, fn, args)
        try:
            conn.send_bytes(reply)
        except (OSError, ValueError):
            return
        del reply, fn, args, message


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3:])
