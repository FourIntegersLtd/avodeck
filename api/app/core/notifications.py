"""Messages to people: in the app and by email (architecture 10.9, behaviour index `notifications`, F33).

Slices call `notify()` with a short `kind` code; the web app turns the kind into words. The person's
choices for that organisation decide the channels: in the app, an email at once, or one email a day.
A muted kind goes nowhere. Emails carry a template and the kind only, never a case name or case
text. A bounced address gets no email until it changes. Who may notify, and whom, is checked by
`app.notify()` in the database (a case the caller can see, or a job's own case).

`send_daily` is the worker's daily schedule (keyed by date, F28): one email per "one email a day"
person with a count, and messages over a year old are deleted.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from psycopg import AsyncConnection

from app.core.email import NOTIFICATION_DIGEST, NOTIFICATION_NEW, Mail, Mailer
from app.core.jobs import registry

logger = logging.getLogger("app.notifications")

NOTIFY_TEMPLATE = NOTIFICATION_NEW
DIGEST_TEMPLATE = NOTIFICATION_DIGEST
DAILY = "notifications_daily"


async def _send(mailer: Mailer, mail: Mail) -> None:
    """Email is best effort: a failure is logged by type only and the message stays in the app."""
    try:
        await mailer.send(mail)
    except Exception as exc:  # noqa: BLE001 - any mail failure must not undo the message itself
        logger.warning("email not sent: %s", type(exc).__name__)


async def notify(
    conn: AsyncConnection,
    mailer: Mailer,
    *,
    person_id: uuid.UUID,
    organisation_id: uuid.UUID,
    kind: str,
    case_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Tells one person; returns the message id, or None when their choices send it nowhere."""
    # In a savepoint: a refusal leaves the caller's transaction usable.
    async with conn.transaction():
        cur = await conn.execute(
            "select notification_id, email from app.notify(%s, %s, %s, %s)",
            (person_id, organisation_id, case_id, kind),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    message_id, email = row
    if email:
        await _send(mailer, Mail(to=email, template=NOTIFY_TEMPLATE, params={"kind": kind}))
    return message_id  # type: ignore[no-any-return]


async def send_daily(conn: AsyncConnection, run_date: date, mailer: Mailer) -> int:
    """Sends the day's "one email a day" messages; returns how many emails went."""
    cur = await conn.execute("select email, message_count from app.notifications_daily(%s)", (run_date,))
    rows = await cur.fetchall()
    for email, count in rows:
        await _send(mailer, Mail(to=email, template=DIGEST_TEMPLATE, params={"count": str(count)}))
    return len(rows)


async def mark_bounced(conn: AsyncConnection, email: str) -> bool:
    """Records that mail to `email` bounced; False when no one has that address."""
    cur = await conn.execute("select app.mark_email_bounced(%s)", (email,))
    row = await cur.fetchone()
    return bool(row and row[0])


def default_mailer() -> Mailer:
    """The worker's mailer: the local stand-in until the cell has the email service (stage 2)."""
    from app.core.email import OutboxMailer

    return OutboxMailer()


@registry.schedule(DAILY)
async def daily_messages(conn: AsyncConnection, run_date: date) -> None:
    await send_daily(conn, run_date, default_mailer())
