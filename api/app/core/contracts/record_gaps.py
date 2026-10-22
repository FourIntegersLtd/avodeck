"""Where a record source goes quiet (slice A's `record_gaps`), read by other slices without importing slice A.

Slice A registers its reader with `@register_reader`; slice D's timeline asks
`await gaps_overlapping(conn, case_id, start, end)` to tell "nothing was done" from "the records go quiet
here". Until a reader is registered the answer is no gaps, so a cell without slice A still draws a timeline.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

from psycopg import AsyncConnection


@dataclass(frozen=True)
class RecordGap:
    source_type: str
    fact_kind: str | None
    gap_start: date
    gap_end: date


Reader = Callable[[AsyncConnection, uuid.UUID, date, date], Awaitable[list[RecordGap]]]


class _Slot:
    """One reader, registered once by slice A; tests may swap it for a while."""

    def __init__(self) -> None:
        self._impl: Reader | None = None

    def register(self, fn: Reader) -> Reader:
        if self._impl is not None and self._impl is not fn:
            raise RuntimeError("record_gaps reader is already registered")
        self._impl = fn
        return fn

    def get(self) -> Reader | None:
        return self._impl

    @contextmanager
    def override(self, fn: Reader | None) -> Iterator[None]:
        previous, self._impl = self._impl, fn
        try:
            yield
        finally:
            self._impl = previous


reader = _Slot()


def register_reader(fn: Reader) -> Reader:
    return reader.register(fn)


async def gaps_overlapping(conn: AsyncConnection, case_id: uuid.UUID, start: date, end: date) -> list[RecordGap]:
    """Gaps that overlap `start`..`end` (inclusive), any source; none until slice A registers a reader."""
    read = reader.get()
    return [] if read is None else await read(conn, case_id, start, end)
