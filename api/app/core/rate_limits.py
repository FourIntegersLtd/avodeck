"""Per-person limits on the API (scalability PR 12): requests per endpoint per minute, and open event streams.

Requests. Every signed-in request is counted once against its person and endpoint (the method and the route's
path template, never an id) with one upsert, `app.rate_limit_hit()` (migration 0065), and the count is the
same whatever the answer was: probing ids that are not yours is limited like anything else. At the start of
a request `check()` reads the count (`app.rate_limit_peek()`, no lock) and, once it has reached
`API_RATE_PER_MINUTE`, answers 429 `slow_down` with `Retry-After` (the seconds left in the minute); a refused
request is not counted again. The request is counted when it has ended, in a short transaction of its own
(`count()`, via `Database.session_request`'s `after`), never inside its own transaction: the row lock would
make two requests of one person to one endpoint wait for each other, as the parts of an upload do, and a
rollback would take the count with it. Postgres holds the count, so it is shared by every process and
replica. Several requests in flight at once may each pass the check, so the limit is soft by the number
in flight.

Streams. A person may hold `API_MAX_STREAMS_PER_PERSON` live event streams open at once, counted in this
process (with two processes that is up to twice the number, which is acceptable). A lease is taken when a
stream starts and given back when it ends; one nobody gives back (the client left before the body began)
expires with the longest a stream can last, so a leak never locks a person out. Access to the case is
checked before the cap, so a stranger gets 404, never a count (rule 9).

Nothing here sees patient text (rule 6): keys are ids and route templates, messages are fixed.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from contextlib import suppress

from psycopg import AsyncConnection

from app.core.errors import ApiError

_SWEEP_ABOVE = 1000
STREAM_RETRY_SECONDS = 10  # a stream that is refused is worth asking for again in this long


def slow_down(retry_after: int) -> ApiError:
    error = ApiError("slow_down", "Please wait a moment and try again.", 429)
    error.headers = {"Retry-After": str(max(1, retry_after))}
    return error


def too_many_streams() -> ApiError:
    error = ApiError("too_many_streams", "Avodeck is open in too many places. Close one and try again.", 429)
    error.headers = {"Retry-After": str(STREAM_RETRY_SECONDS)}
    return error


async def check(conn: AsyncConnection, endpoint: str, limit: int) -> None:
    """Turn the request away (429 `slow_down`) when this person has already made `limit` requests to this
    endpoint in this minute (0 = no limit). A plain read: it takes no lock."""
    cur = await conn.execute("select hits, retry_after from app.rate_limit_peek(%s)", (endpoint,))
    row = await cur.fetchone()
    if row is not None and row[0] >= limit:
        raise slow_down(row[1])


async def count(conn: AsyncConnection, person_id: uuid.UUID, endpoint: str) -> None:
    """Count one request, in a short transaction of its own on a connection nobody is using: the request's
    own transaction has ended (or its connection was given back), so the count's row lock is held for this
    statement only. Best effort: it never raises, and the answer the person was about to get does not change."""
    try:
        async with conn.pipeline():
            await conn.execute("BEGIN")
            await conn.execute("select set_config('app.person_id', %s, true)", (str(person_id),))
            await conn.execute("select hits from app.rate_limit_hit(%s)", (endpoint,))
            await conn.execute("COMMIT")
    except Exception:  # noqa: BLE001
        with suppress(Exception):
            if not conn.closed:
                await conn.execute("ROLLBACK")


class StreamLease:
    """One open stream. `release()` is safe to call more than once."""

    def __init__(self, cap: StreamCap, person_id: uuid.UUID) -> None:
        self._cap = cap
        self._person_id = person_id
        self.expires_at = 0.0
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._cap._drop(self._person_id, self)


class StreamCap:
    """How many event streams each person has open in this process."""

    def __init__(self, limit: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self._clock = clock
        self._open: dict[uuid.UUID, list[StreamLease]] = {}

    def acquire(self, person_id: uuid.UUID, *, lifetime: float, limit: int | None = None) -> StreamLease:
        """A place for one more stream, or 429 `too_many_streams`. `lifetime` is the longest the stream can last."""
        cap = self.limit if limit is None else limit
        now = self._clock()
        held = [lease for lease in self._open.get(person_id, []) if lease.expires_at > now]
        if cap > 0 and len(held) >= cap:
            self._open[person_id] = held
            raise too_many_streams()
        if len(self._open) > _SWEEP_ABOVE:  # people who left without a clean end: forget their expired leases
            alive = {pid: [x for x in ls if x.expires_at > now] for pid, ls in self._open.items()}
            self._open = {pid: ls for pid, ls in alive.items() if ls}
        lease = StreamLease(self, person_id)
        lease.expires_at = now + lifetime
        held.append(lease)
        self._open[person_id] = held
        return lease

    def _drop(self, person_id: uuid.UUID, lease: StreamLease) -> None:
        held = [other for other in self._open.get(person_id, []) if other is not lease]
        if held:
            self._open[person_id] = held
        else:
            self._open.pop(person_id, None)

    def open_count(self, person_id: uuid.UUID) -> int:
        now = self._clock()
        return sum(1 for lease in self._open.get(person_id, []) if lease.expires_at > now)

    def people_tracked(self) -> int:
        return len(self._open)
