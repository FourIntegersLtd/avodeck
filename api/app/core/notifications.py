"""Messages to people: in the app and by email (architecture 10.9, behaviour index `notifications`, F33).

Slices call `notify()` with a short `kind` code; the web app turns the kind into words. The person's
choices for that organisation decide the channels: in the app, an email at once, or one email a day.
A muted kind goes nowhere. Emails carry a template and the kind only, never a case name or case
text. A bounced address gets no email until it changes. Who may notify, and whom, is checked by
`app.notify()` in the database (a case the caller can see, or a job's own case).

A message about a case on hold is kept back (not listed, not emailed) until the case resumes
(`app.core.case_hold.release_case_hold`); `while_on_hold=True` is for a message about the hold itself.

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

# Email is for what matters: something the person must act on, their own security or account, or a
# permanent record. Most have their own templates and are sent where they happen (invite, invoice, email
# change, backup code, account created, support access, spot-check code, certificate of deletion, a case
# newly assigned). A notification `kind` reaches email at once only if it is listed here or starts with a
# listed prefix. Deadline reminders do: a court deadline a few days away is worth an email. The kind after
# the prefix is the pack's own word, so only the prefix is named here (rule 11). Stage and progress news
# (bundle_ready, deletion_paused, case.reassigned) stays in the app, and in the daily email for people who
# chose one. Add a kind only with foundation's approval.
EMAIL_KINDS: frozenset[str] = frozenset()
EMAIL_KIND_PREFIXES: frozenset[str] = frozenset({"deadline."})


def emails_at_once(kind: str) -> bool:
    return kind in EMAIL_KINDS or any(
        kind.startswith(prefix) and len(kind) > len(prefix) for prefix in EMAIL_KIND_PREFIXES
    )


async def send_mail(mailer: Mailer, mail: Mail) -> None:
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
    while_on_hold: bool = False,
) -> uuid.UUID | None:
    """Tells one person; returns the message id, or None when their choices send it nowhere."""
    # In a savepoint: a refusal leaves the caller's transaction usable.
    async with conn.transaction():
        cur = await conn.execute(
            "select notification_id, email from app.notify(%s, %s, %s, %s, %s)",
            (person_id, organisation_id, case_id, kind, while_on_hold),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    message_id, email = row
    if email and emails_at_once(kind):
        await send_mail(mailer, Mail(to=email, template=NOTIFY_TEMPLATE, params={"kind": kind}))
    return message_id  # type: ignore[no-any-return]


async def send_daily(conn: AsyncConnection, run_date: date, mailer: Mailer) -> int:
    """Sends the day's "one email a day" messages; returns how many emails went."""
    cur = await conn.execute("select email, message_count from app.notifications_daily(%s)", (run_date,))
    rows = await cur.fetchall()
    for email, count in rows:
        await send_mail(mailer, Mail(to=email, template=DIGEST_TEMPLATE, params={"count": str(count)}))
    return len(rows)


async def mark_bounced(conn: AsyncConnection, email: str) -> bool:
    """Records that mail to `email` bounced; False when no one has that address."""
    cur = await conn.execute("select app.mark_email_bounced(%s)", (email,))
    row = await cur.fetchone()
    return bool(row and row[0])


def default_mailer() -> Mailer:
    """The worker's mailer: Resend when the cell has a key, the local outbox otherwise."""
    from app.core.email_live import mailer_from_env

    return mailer_from_env()


@registry.schedule(DAILY)
async def daily_messages(conn: AsyncConnection, run_date: date) -> None:
    await send_daily(conn, run_date, default_mailer())
