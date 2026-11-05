"""One LISTEN connection per API process that wakes the open streams of a case (architecture 5.3, PR 8).

Migration 0062 makes every change to a job's progress `pg_notify('case_jobs', '<case id>')`. The payload
is a case uuid and nothing else (rule 6). `CaseNotifier` holds the single connection that LISTENs, keeps a
set of `asyncio.Event`s per case, and sets them when that case is named, so a stream looks at the database
when something changed instead of once a second. The notification only says "look again": the stream still
reads its own state as the signed-in person under row-level security and re-checks access (rule 9).

The connection is its own (autocommit, not from the pool) because a pooled transaction-mode connection
cannot LISTEN: `DATABASE_URL_LISTEN` must be a direct or session-mode URL as `app_api`. It reconnects with
backoff when it drops, and wakes every stream once after each (re)connect, so a change that landed while it
was away is not missed. A stream also keeps a slow safety tick, so a lost notification costs seconds, not
a stuck screen.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress

from psycopg import AsyncConnection

CHANNEL = "case_jobs"
LISTENER_APPLICATION_NAME = "avodeck-case-listener"

_log = logging.getLogger(__name__)


class CaseNotifier:
    def __init__(
        self,
        dsn: str,
        *,
        backoff_min: float = 0.5,
        backoff_max: float = 15.0,
        ping_seconds: float = 20.0,
    ) -> None:
        self._dsn = dsn
        self._backoff_min = backoff_min
        self._backoff_max = backoff_max
        self._ping = ping_seconds  # how often a quiet connection is tested, so a silent drop is noticed
        self._events: dict[uuid.UUID, set[asyncio.Event]] = {}
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def ready(self) -> bool:
        """True while the connection is listening."""
        return self._ready.is_set()

    async def wait_ready(self, timeout: float) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout)

    @contextmanager
    def watch(self, case_id: uuid.UUID) -> Iterator[asyncio.Event]:
        """Subscribe to a case; the event is set when its jobs change, and the subscription ends with the block."""
        event = asyncio.Event()
        self._events.setdefault(case_id, set()).add(event)
        try:
            yield event
        finally:
            subscribed = self._events.get(case_id)
            if subscribed is not None:
                subscribed.discard(event)
                if not subscribed:
                    del self._events[case_id]

    def watching(self, case_id: uuid.UUID) -> int:
        return len(self._events.get(case_id, ()))

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="case-notifier")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _wake(self, payload: str) -> None:
        try:
            case_id = uuid.UUID(payload)
        except ValueError:
            return  # not a case id: ignored, and never logged (it could be anything)
        for event in tuple(self._events.get(case_id, ())):
            event.set()

    def _wake_all(self) -> None:
        for subscribed in tuple(self._events.values()):
            for event in tuple(subscribed):
                event.set()

    async def _run(self) -> None:
        delay = self._backoff_min
        while True:
            try:
                async with await AsyncConnection.connect(
                    self._dsn, autocommit=True, application_name=LISTENER_APPLICATION_NAME, connect_timeout=10
                ) as conn:
                    await conn.execute(f"listen {CHANNEL}")
                    delay = self._backoff_min
                    self._ready.set()
                    self._wake_all()  # a change while we were away is not missed
                    while True:
                        async for note in conn.notifies(timeout=self._ping):
                            self._wake(note.payload)
                        await conn.execute("select 1")  # a dead connection raises here
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect whatever the cause; the type alone is logged
                _log.warning("case notifier connection lost: %s", type(exc).__name__)
            finally:
                self._ready.clear()
            # Jitter, so a restarted fleet does not reconnect in step.
            await asyncio.sleep(delay * random.uniform(0.5, 1.0))
            delay = min(delay * 2, self._backoff_max)
