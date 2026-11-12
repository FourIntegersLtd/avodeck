"""Top-level functions the parser-sandbox tests run in a child process (they must be importable there).

Synthetic only: the canary strings stand for patient text and must never come back in an error or a log.
"""

from __future__ import annotations

import io
import logging
import os
import sys
import time


def add(a: int, b: int) -> int:
    return a + b


def echo(data: bytes) -> bytes:
    return data


def pid() -> int:
    return os.getpid()


def sleep_for(seconds: float) -> int:
    time.sleep(seconds)
    return os.getpid()


def allocate(size: int) -> int:
    block = bytearray(size)
    for i in range(0, size, 4096):
        block[i] = 1
    return len(block)


def hard_exit(code: int = 7) -> None:
    os._exit(code)


def raise_value(message: str) -> None:
    raise ValueError(message)


class Unpicklable(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.handle = lambda: message  # a lambda cannot be pickled, so this exception cannot cross the pipe


def raise_unpicklable(message: str) -> None:
    raise Unpicklable(message)


def unpicklable_result(message: str) -> object:
    return lambda: message


def chatter_then_fail(canary: str) -> None:
    print(canary)
    print(canary, file=sys.stderr)
    logging.getLogger().error("canary %s", canary)
    logging.getLogger("app").error("canary %s", canary)
    raise Unpicklable(canary)


def open_encrypted_pdf(pdf: bytes) -> int:
    import pikepdf

    with pikepdf.open(io.BytesIO(pdf)) as opened:
        return len(opened.pages)
