"""The bundle pipeline's entry points (slice A implements them; B and G call them).

Queue with `registry.enqueue(conn, case_id, PROCESS_BUNDLE)` (whole bundle) or `PROCESS_PAGES`
(appended pages only). Pages keep their numbers forever; appended pages continue from the last.

Typed entry point: slice A registers its implementation with `@register_process_pages`; slices B
(rotation, transcription) and G (new records) call `await process_pages(conn, case_id, page_ids)`
without importing slice A. It queues the work on the caller's connection and returns the job ID.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Protocol

from psycopg import AsyncConnection

PROCESS_BUNDLE = "process_bundle"
PROCESS_PAGES = "process_pages"


class ProcessPages(Protocol):
    def __call__(
        self, conn: AsyncConnection, case_id: uuid.UUID, page_ids: Sequence[uuid.UUID]
    ) -> Coroutine[Any, Any, uuid.UUID]: ...


class EntryPointMissing(LookupError):
    """Nothing implements this entry point yet."""


class EntryPointTaken(RuntimeError):
    """Another implementation is already registered for this entry point."""


class EntryPoint[F: Callable[..., Any]]:
    """One slot that exactly one slice fills."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._impl: F | None = None

    def register(self, fn: F) -> F:
        if self._impl is not None and self._impl is not fn:
            raise EntryPointTaken(f"{self.name} is already implemented")
        self._impl = fn
        return fn

    def get(self) -> F:
        if self._impl is None:
            raise EntryPointMissing(f"{self.name} is not implemented")
        return self._impl

    @contextmanager
    def override(self, fn: F) -> Iterator[F]:
        """For tests: use `fn` for the duration, then restore what was there."""
        previous, self._impl = self._impl, fn
        try:
            yield fn
        finally:
            self._impl = previous


process_pages_entry: EntryPoint[ProcessPages] = EntryPoint(PROCESS_PAGES)


def register_process_pages(fn: ProcessPages) -> ProcessPages:
    return process_pages_entry.register(fn)


async def process_pages(conn: AsyncConnection, case_id: uuid.UUID, page_ids: Sequence[uuid.UUID]) -> uuid.UUID:
    return await process_pages_entry.get()(conn, case_id, page_ids)
