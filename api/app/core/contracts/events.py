"""In-process events between slices. `ReportServed` is emitted by F when a report is served; G listens."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, TypeVar

E = TypeVar("E")


@dataclass(frozen=True)
class ReportServed:
    case_id: uuid.UUID
    report_version_id: uuid.UUID
    served_on: date


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: type[E]) -> Callable[[Callable[[E], None]], Callable[[E], None]]:
        def register(handler: Callable[[E], None]) -> Callable[[E], None]:
            self._handlers[event_type].append(handler)
            return handler

        return register

    def emit(self, event: object) -> None:
        """Call each subscriber in the order it subscribed."""
        for handler in self._handlers[type(event)]:
            handler(event)


events = EventBus()
