"""Durable after-commit work (audit 15, BG-16): written in the request's transaction, delivered by the worker.

FastAPI background tasks are lost when the API stops between the commit and the task. A route that must not lose its
follow-up calls `defer_mail(conn, mail)` or `defer_call(conn, name, args)` on its own connection instead: the row
commits with the request, or not at all, and a worker sweep (`SWEEP`, every housekeeping round) delivers it with
retry. Delivery is at least once: a send whose answer is lost is tried again, and a mail carries its row id as the mail
service's idempotency key so the repeat does not send a second email.

Nothing secret or personal is stored in the clear: the payload (address, one-time code, link, call arguments) is
sealed under the cell key and bound to the row id; the row keeps a template or call name, a status and counts, and the
payload goes when the row ends. Only exception type names are logged or stored (rule 6). A row lives at most 7 days;
`valid_for` (default 24 hours) is when it stops being worth sending (a one-time code or a link: its own lifetime).

A call is a named async handler `(conn, args)` that a slice registers with `@deferred.call("feature.name")` (imported
by both the API and the worker); `args` are short strings. It must be safe to run twice.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import timedelta

from psycopg import AsyncConnection

from app.core.crypto import SecretBox, cell_secret_box
from app.core.email import TEMPLATES, Mail, Mailer, check_params
from app.core.jobs import registry

logger = logging.getLogger(__name__)

SWEEP = "deferred_work"
PURPOSE = "deferred_work"
DEFAULT_VALIDITY = timedelta(hours=24)
BATCH = 20

CallHandler = Callable[[AsyncConnection, dict[str, str]], Awaitable[None]]
calls: dict[str, CallHandler] = {}


def call(name: str) -> Callable[[CallHandler], CallHandler]:
    """Register a named call a route can defer (`defer_call`). Names are `feature.what`, lower case."""

    def register(handler: CallHandler) -> CallHandler:
        if name in calls:
            raise ValueError(f"deferred call {name!r} is registered twice")
        calls[name] = handler
        return handler

    return register


async def _defer(
    conn: AsyncConnection,
    kind: str,
    name: str,
    payload: Mapping[str, object],
    valid_for: timedelta,
    box: SecretBox | None,
) -> uuid.UUID:
    row_id = uuid.uuid4()
    sealed = (box or cell_secret_box()).seal(json.dumps(payload), purpose=PURPOSE, subject=str(row_id))
    cur = await conn.execute(
        "select app.defer_work(%s, %s, %s, %s, now() + %s)", (row_id, kind, name, sealed, valid_for)
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


async def defer_mail(
    conn: AsyncConnection, mail: Mail, *, valid_for: timedelta = DEFAULT_VALIDITY, box: SecretBox | None = None
) -> uuid.UUID:
    """Write `mail` to be sent by the worker once the request commits. The mail is checked now, so a bad one fails the
    request instead of failing later where no one sees it."""
    if mail.template not in TEMPLATES:
        raise ValueError(f"unknown email template {mail.template!r}")
    check_params(mail)
    payload = {"to": mail.to, "template": mail.template, "params": mail.params, "reply_to": mail.reply_to}
    return await _defer(conn, "mail", mail.template, payload, valid_for, box)


async def defer_call(
    conn: AsyncConnection,
    name: str,
    args: Mapping[str, str],
    *,
    valid_for: timedelta = DEFAULT_VALIDITY,
    box: SecretBox | None = None,
) -> uuid.UUID:
    """Write a call to the registered handler `name` to run in the worker once the request commits."""
    if name not in calls:
        raise ValueError(f"no deferred call named {name!r}")
    return await _defer(conn, "call", name, dict(args), valid_for, box)


async def _run(
    conn: AsyncConnection, mailer: Mailer, row_id: uuid.UUID, kind: str, name: str, sealed: str, box: SecretBox
) -> None:
    payload = json.loads(box.open(sealed, purpose=PURPOSE, subject=str(row_id)))
    async with conn.transaction():  # a database error in a handler must not end the sweep's transaction
        if kind == "mail":
            mail = Mail(
                to=payload["to"], template=payload["template"], params=payload["params"], reply_to=payload["reply_to"]
            )
            await mailer.send(replace(mail, idempotency_key=str(row_id)))
        else:
            await calls[name](conn, payload)


async def deliver(conn: AsyncConnection, mailer: Mailer, *, limit: int = BATCH, box: SecretBox | None = None) -> int:
    """Deliver the due rows on the worker's connection; returns how many went. The rows stay locked to this
    transaction, so a crash puts them back and another sweep skips them meanwhile."""
    box = box or cell_secret_box()
    cur = await conn.execute("select id, kind, name, sealed from app.take_deferred_work(%s)", (limit,))
    sent = 0
    for row_id, kind, name, sealed in await cur.fetchall():
        error: str | None = None
        try:
            await _run(conn, mailer, row_id, kind, name, sealed, box)
            sent += 1
        except Exception as exc:  # noqa: BLE001 - kept for the next try; the type only, a message may hold an address
            error = type(exc).__name__
            logger.warning("deferred %s %s failed: %s", kind, name, error)
        await conn.execute("select app.finish_deferred_work(%s, %s, %s)", (row_id, error is None, error))
    return sent


async def prune(conn: AsyncConnection) -> int:
    """Delete rows that ended more than a week ago (they hold no payload)."""
    cur = await conn.execute("select app.prune_deferred_work(7)")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


@registry.sweep(SWEEP)
async def _sweep(conn: AsyncConnection) -> None:
    from app.core.notifications import default_mailer

    await deliver(conn, default_mailer())
    await prune(conn)


__all__ = ["SWEEP", "call", "calls", "defer_call", "defer_mail", "deliver", "prune"]
