"""Releasing what a case's hold kept back (state-machines.md "On hold"; request from slice C).

While a case is on hold the worker does not run its jobs (they stay `queued` with `case_on_hold`)
and messages about it are kept back. When the case resumes, the route calls `release_case_hold()`
in the same transaction: the database puts the held jobs back on the queue and lists the held
messages again; this sends the emails due at once (the rest go in the daily email). Email is best
effort. Only someone who may resume the case (`case.hold`) may release it, and only once it is off
hold (409 `AV409` otherwise).
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core.email import Mail, Mailer
from app.core.notifications import NOTIFY_TEMPLATE, send_mail


async def release_case_hold(conn: AsyncConnection, mailer: Mailer, case_id: uuid.UUID) -> int:
    """Releases the case's held jobs and messages; returns how many emails went."""
    cur = await conn.execute("select email, kind from app.release_case_hold(%s)", (case_id,))
    rows = await cur.fetchall()
    for email, kind in rows:
        await send_mail(mailer, Mail(to=email, template=NOTIFY_TEMPLATE, params={"kind": kind}))
    return len(rows)
