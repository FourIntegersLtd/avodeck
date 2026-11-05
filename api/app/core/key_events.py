"""Who is told when a case key is destroyed (scalability PR 15).

A case key is cached for up to 5 minutes in every API and worker process, and the reader keeps a case's
decrypted words for up to 10. When the key is destroyed each of those caches must drop that case at once, on
every process. Core cannot import a feature, so each cache subscribes here: the key cache in `core/crypto.py`
and (from its own module) the reader's search cache. `core/notify.CaseNotifier` calls `destroyed` when the
database says a key was destroyed, and `forget_all` after its connection was away, because a message sent
meanwhile was missed.

Only a case id travels (rule 6). A subscriber that raises is skipped, never allowed to stop the others, and
nothing it raises is logged beyond its type.
"""

from __future__ import annotations

import inspect
import logging
import uuid
import weakref
from collections.abc import Callable

_log = logging.getLogger(__name__)

type _Ref[T] = Callable[[], T | None]


def _ref[T: Callable[..., object]](fn: T) -> _Ref[T]:
    """A bound method is held weakly so a subscriber that is garbage-collected is not kept alive by the registry."""
    if inspect.ismethod(fn):
        return weakref.WeakMethod(fn)
    return lambda: fn


class KeyEvents:
    def __init__(self) -> None:
        self._subscribers: list[tuple[_Ref[Callable[[uuid.UUID], None]], _Ref[Callable[[], None]] | None]] = []

    @property
    def subscribers(self) -> int:
        self._prune()
        return len(self._subscribers)

    def subscribe(self, on_case: Callable[[uuid.UUID], None], on_all: Callable[[], None] | None = None) -> None:
        """`on_case(case_id)` when that case's key was destroyed; `on_all()` when any may have been missed."""
        self._subscribers.append((_ref(on_case), _ref(on_all) if on_all is not None else None))

    def destroyed(self, case_id: uuid.UUID) -> None:
        for on_case, _ in self._live():
            fn = on_case()
            if fn is not None:
                self._call(fn, case_id)

    def forget_all(self) -> None:
        for _, on_all in self._live():
            fn = on_all() if on_all is not None else None
            if fn is not None:
                self._call(fn)

    def _live(self) -> list[tuple[_Ref[Callable[[uuid.UUID], None]], _Ref[Callable[[], None]] | None]]:
        self._prune()
        return list(self._subscribers)

    def _prune(self) -> None:
        self._subscribers = [s for s in self._subscribers if s[0]() is not None]

    @staticmethod
    def _call(fn: Callable[..., None], *args: object) -> None:
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - one broken cache must not stop the others; the type alone is logged
            _log.warning("key destroyed handler failed: %s", type(exc).__name__)


# The registry every process shares; tests build their own to stand in for separate processes.
key_events = KeyEvents()
