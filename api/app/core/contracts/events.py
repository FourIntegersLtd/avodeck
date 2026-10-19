"""In-process events between slices.

- `ReportServed` is emitted by F when a report is served; G listens (`subscribe` / `emit`). `served_by`
  is the person who served; G's handler acts as them and refuses an event without one.
- `CaseWithdrawn` is published by C inside the withdraw transaction; each subscriber that cancels
  something for the case (G's deadlines) appends how many to `cancelled`, which C reports.
- `DocumentUploaded` is published by core when an upload completes, inside the request's
  transaction; A listens to queue scanning and processing (`subscribe_in_transaction` / `publish`).
  An in-transaction subscriber gets the same connection, so what it writes commits with the
  document or not at all; if it raises, the request fails and nothing is registered.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypeVar

from psycopg import AsyncConnection

E = TypeVar("E")


@dataclass(frozen=True)
class ReportServed:
    case_id: uuid.UUID
    report_version_id: uuid.UUID
    served_on: date
    served_by: uuid.UUID | None = None


@dataclass(frozen=True)
class CaseWithdrawn:
    case_id: uuid.UUID
    cancelled: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class DocumentUploaded:
    case_id: uuid.UUID
    document_id: uuid.UUID
    appended: bool = False


InTransaction = Callable[[AsyncConnection, Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)
        self._in_transaction: dict[type, list[InTransaction]] = defaultdict(list)

    def subscribe(self, event_type: type[E]) -> Callable[[Callable[[E], None]], Callable[[E], None]]:
        def register(handler: Callable[[E], None]) -> Callable[[E], None]:
            self._handlers[event_type].append(handler)
            return handler

        return register

    def emit(self, event: object) -> None:
        """Call each subscriber in the order it subscribed."""
        for handler in self._handlers[type(event)]:
            handler(event)

    def subscribe_in_transaction(
        self, event_type: type[E]
    ) -> Callable[[Callable[[AsyncConnection, E], Awaitable[None]]], Callable[[AsyncConnection, E], Awaitable[None]]]:
        def register(
            handler: Callable[[AsyncConnection, E], Awaitable[None]],
        ) -> Callable[[AsyncConnection, E], Awaitable[None]]:
            self._in_transaction[event_type].append(handler)
            return handler

        return register

    def unsubscribe(self, event_type: type, handler: Callable[..., Any]) -> None:
        for handlers in (self._handlers[event_type], self._in_transaction[event_type]):
            if handler in handlers:
                handlers.remove(handler)

    async def publish(self, conn: AsyncConnection, event: object) -> None:
        """Await each in-transaction subscriber in order, on the caller's connection; errors propagate."""
        for handler in list(self._in_transaction[type(event)]):
            await handler(conn, event)


events = EventBus()
