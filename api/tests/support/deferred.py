"""Test helper for durable after-commit work (BG-16): run the worker's delivery once, as the sweep does.

A route that calls `deferred.defer_mail(conn, mail)` no longer sends the mail itself; the mail reaches a mailer only
when the worker sweep runs. A test that checked `mailer.outbox` straight after the request calls `drain(...)` first.
"""

from __future__ import annotations

from app.core import deferred
from app.core.db import Database
from app.core.email import Mailer


async def drain(worker_dsn: str, mailer: Mailer) -> int:
    """Deliver every due deferred row to `mailer` (one worker sweep); returns how many went."""
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        async with database.anonymous() as conn:
            return await deferred.deliver(conn, mailer)
    finally:
        await database.close()
