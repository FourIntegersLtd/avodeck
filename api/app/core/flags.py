"""Feature flags (architecture 4.11, 8.2): unfinished work ships switched off.

Flags are set by staff (never through the API) at four levels. For a person, the most specific
level that sets a flag wins: their own person flag, then any of their current organisations (on if
any says on), then this cell, then global. Rows are read under row-level security, so a person only
ever sees flags that apply to them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from psycopg import AsyncConnection

from app.core.errors import ApiError
from app.core.http import DbConn

_ORDER = {"global": 0, "cell": 1, "organisation": 2, "person": 3}


async def evaluate(conn: AsyncConnection) -> dict[str, bool]:
    """Every flag that applies to the current person (or job), worked out."""
    cur = await conn.execute("select key, scope, enabled from app.feature_flags")
    best: dict[str, tuple[int, bool]] = {}
    for key, scope, enabled in await cur.fetchall():
        level = _ORDER[scope]
        found = best.get(key)
        if found is None or level > found[0]:
            best[key] = (level, enabled)
        elif level == found[0]:
            best[key] = (level, found[1] or enabled)
    return {key: on for key, (_, on) in sorted(best.items())}


async def is_enabled(conn: AsyncConnection, key: str) -> bool:
    return (await evaluate(conn)).get(key, False)


def require_flag(key: str) -> Callable[[DbConn], Awaitable[None]]:
    """A route dependency: 404 until `key` is on for this person."""

    async def check(conn: DbConn) -> None:
        if not await is_enabled(conn, key):
            raise ApiError("not_found", "Not found.", 404)

    return check
