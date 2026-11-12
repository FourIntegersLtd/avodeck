"""Stand-ins for the pipeline's parser calls that hang or kill their own process (audit 13, DP-09).

They are top-level functions so a sandbox child can import them. Each takes the arguments of the real call it
replaces and does the real work unless its input carries the marker, so only the chosen document or page misbehaves.
Synthetic only: the markers are made-up words, never patient text.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from app.core.pdf import embedded_words
from app.features.pipeline import convert, parsing

MARK = "KILLME"  # a word on the page that makes a page-level stand-in misbehave
FILE_MARK = b"%KILLME"  # trailing bytes that make a document-level stand-in misbehave
FILE_NAME = "killme"  # a file name prefix that makes the unpack stand-in misbehave


def _hang() -> None:
    time.sleep(120)


def _die() -> None:
    os._exit(1)


def _marked(page_pdf: bytes) -> bool:
    return any(MARK in word.text for word in embedded_words(page_pdf).words)


def count_hang(path: str, max_pages: int) -> int:
    if FILE_MARK in Path(path).read_bytes():
        _hang()
    return parsing.count_pages(path, max_pages)


def count_exit(path: str, max_pages: int) -> int:
    if FILE_MARK in Path(path).read_bytes():
        _die()
    return parsing.count_pages(path, max_pages)


def unpack_hang(data: bytes, filename: str, limits: convert.Limits) -> convert.Unpacked:
    if filename.startswith(FILE_NAME):
        _hang()
    return parsing.unpack(data, filename, limits)


def unpack_exit(data: bytes, filename: str, limits: convert.Limits) -> convert.Unpacked:
    if filename.startswith(FILE_NAME):
        _die()
    return parsing.unpack(data, filename, limits)


def cut_hang(path: str, indexes: list[int]) -> list[bytes]:
    pages = parsing.cut_pages(path, indexes)
    if any(_marked(p) for p in pages):
        _hang()
    return pages


def cut_exit(path: str, indexes: list[int]) -> list[bytes]:
    pages = parsing.cut_pages(path, indexes)
    if any(_marked(p) for p in pages):
        _die()
    return pages


def pictures_hang(page_pdf: bytes, rotation: int) -> Any:
    if _marked(page_pdf):
        _hang()
    return parsing.pictures(page_pdf, rotation)


def pictures_exit(page_pdf: bytes, rotation: int) -> Any:
    if _marked(page_pdf):
        _die()
    return parsing.pictures(page_pdf, rotation)


def layer_hang(page_pdf: bytes) -> Any:
    if _marked(page_pdf):
        _hang()
    return parsing.text_layer(page_pdf)


def layer_exit(page_pdf: bytes) -> Any:
    if _marked(page_pdf):
        _die()
    return parsing.text_layer(page_pdf)


def pictures_slow(page_pdf: bytes, rotation: int) -> Any:
    """A big scan: the drawing takes two seconds, in the child, so the worker's loop is not held."""
    time.sleep(2)
    return parsing.blank_pictures()


def pictures_crash(page_pdf: bytes, rotation: int) -> Any:
    """A page that makes the drawing raise: a bug, not a hang or a death (the job fails and is retried)."""
    if _marked(page_pdf):
        raise RuntimeError("synthetic crash")
    return parsing.pictures(page_pdf, rotation)


def span(seconds: float) -> tuple[int, float, float]:
    """The child's pid and the interval (monotonic clock) in which this call ran."""
    started = time.monotonic()
    time.sleep(seconds)
    return os.getpid(), started, time.monotonic()
