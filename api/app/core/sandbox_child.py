"""The child side of `app.core.sandbox`: `python -m app.core.sandbox_child <fd> <memory_bytes> [module ...]`.

Imports only the standard library, so a child starts fast and carries nothing of the worker (no settings, no
Sentry, no log handlers). It reads `(fn, args)` from the socket the parent passed, calls `fn(*args)` and sends back
`("ok", result)`, `("raised", exception)`, `("memory", None)` or `("failed", None)`. It never writes to stdout or
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


def _reply(fn: Any, args: tuple[Any, ...]) -> tuple[str, Any]:
    try:
        reply: tuple[str, Any] = ("ok", fn(*args))
        try:
            pickle.dumps(reply)
        except BaseException:  # noqa: BLE001 - a result that cannot cross the pipe; say nothing about it
            return ("failed", None)
        return reply
    except MemoryError:
        return ("memory", None)
    except BaseException as exc:  # noqa: BLE001 - everything goes back to the parent as data
        try:
            pickle.loads(pickle.dumps(exc))
        except BaseException:  # noqa: BLE001 - this exception cannot cross the pipe
            return ("failed", None)
        return ("raised", exc)


def main(fd: int, memory_bytes: int, modules: list[str] | None = None) -> None:
    conn = Connection(fd)
    _lock_down(memory_bytes)
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
        fn, args = message
        reply = _reply(fn, args)
        try:
            conn.send(reply)
        except (OSError, ValueError):
            return
        except BaseException:  # noqa: BLE001 - the answer could not be pickled after all
            try:
                conn.send(("failed", None))
            except BaseException:  # noqa: BLE001
                return
        del reply, fn, args, message


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3:])
