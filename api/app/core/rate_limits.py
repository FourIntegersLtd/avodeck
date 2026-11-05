"""Per-person limits on the API (scalability PR 12): requests per endpoint per minute, and open event streams.

Requests. Every signed-in request counts once against its person and endpoint (the method and the route's
path template, never an id) with one upsert, `app.rate_limit_hit()` (migration 0065), in the request's own
transaction at the start. No second connection is taken, so a small pool cannot deadlock on it, and nothing
is held across slow work: the count's row lock ends with the transaction, and a route that gives its
connection back early (`release_connection`) ends it sooner. Postgres holds the count, so it is shared by
every process and replica. Over `API_RATE_PER_MINUTE` the answer is 429 `slow_down` with `Retry-After`
(the seconds left in the minute). A failed request's transaction is rolled back, count and all, so a
request that ends in an error is counted again in a short transaction of its own: probing ids that are not
yours is limited like anything else. The refused request itself is not counted again.

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


async def hit(conn: AsyncConnection, endpoint: str, limit: int) -> None:
    """Count this request; 429 `slow_down` once the person is over `limit` in this minute (0 = no limit)."""
    if limit <= 0:
        return
    cur = await conn.execute("select hits, retry_after from app.rate_limit_hit(%s)", (endpoint,))
    row = await cur.fetchone()
    if row is not None and row[0] > limit:
        raise slow_down(row[1])


async def keep(conn: AsyncConnection, person_id: uuid.UUID, endpoint: str) -> None:
    """Count a request whose own transaction was rolled back (it ended in an error), on the same connection.

    Called once that transaction has ended and the connection is still held (`Database.session_request`'s
    `after`), so an error answer still takes one connection. Best effort: it never raises."""
    try:
        async with conn.pipeline():
            await conn.execute("BEGIN")
            await conn.execute("select set_config('app.person_id', %s, true)", (str(person_id),))
            await conn.execute("select hits from app.rate_limit_hit(%s)", (endpoint,))
            await conn.execute("COMMIT")
    except Exception:  # noqa: BLE001 - the answer the person was about to get must not change
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
