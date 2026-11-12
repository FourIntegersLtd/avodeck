"""Durable after-commit work (audit 15, BG-16): written in the request's transaction, delivered by the worker.

FastAPI background tasks are lost when the API stops between the commit and the task. A route that must not lose its
follow-up calls `defer_mail(conn, mail)` or `defer_call(conn, name, args)` on its own connection instead: the row
commits with the request, or not at all, and a worker sweep (`SWEEP`, every housekeeping round, after the others, one
row per short transaction within a time budget) delivers it with retry. Delivery is at least once: a send whose answer
is lost is tried again, and a mail carries its row id as the mail service's idempotency key so the repeat does not
send a second email.

Nothing secret or personal is stored in the clear: the payload (address, one-time code, link, call arguments) is
sealed under the cell key and bound to the row id; the row keeps a template or call name, a status and counts, and the
payload goes when the row ends. Only exception type names are logged or stored (rule 6). A row lives at most 7 days;
`valid_for` (default 24 hours) is when it stops being worth sending (a one-time code or a link: its own lifetime).

A call is a named async handler `(conn, args)` that a slice registers with `@deferred.call("feature.name")` (imported
by both the API and the worker); `args` are short strings. It must be safe to run twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import timedelta

from psycopg import AsyncConnection

from app.core.crypto import SecretBox, cell_secret_box
from app.core.db import Database
from app.core.email import TEMPLATES, Mail, Mailer, check_params
from app.core.jobs import registry

logger = logging.getLogger(__name__)

SWEEP = "deferred_work"
PURPOSE = "deferred_work"
DEFAULT_VALIDITY = timedelta(hours=24)
BATCH = 20
# One worker round spends at most this long on deferred work, and one row at most `ROW_LIMIT_SECONDS` (the mail service
# tries twice with a 10 second timeout): an email outage delays the other housekeeping by seconds, not minutes.
BUDGET_SECONDS = 20.0
ROW_LIMIT_SECONDS = 30.0

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


async def _attempt(
    conn: AsyncConnection,
    mailer: Mailer,
    row: tuple[uuid.UUID, str, str, str],
    box: SecretBox,
    limit_seconds: float,
) -> bool:
    """One delivery attempt for a row this transaction holds, and its outcome written; whether it went."""
    row_id, kind, name, sealed = row
    error: str | None = None
    try:
        async with asyncio.timeout(limit_seconds):  # a hung mail service costs one row this long, not the round
            await _run(conn, mailer, row_id, kind, name, sealed, box)
    except Exception as exc:  # noqa: BLE001 - kept for the next try; the type only, a message may hold an address
        error = type(exc).__name__
        logger.warning("deferred %s %s failed: %s", kind, name, error)
    await conn.execute("select app.finish_deferred_work(%s, %s, %s)", (row_id, error is None, error))
    return error is None


async def deliver(conn: AsyncConnection, mailer: Mailer, *, limit: int = BATCH, box: SecretBox | None = None) -> int:
    """Deliver the due rows on one connection and one transaction; returns how many went. The rows stay locked to
    that transaction, so another sweep skips them meanwhile. The worker does not use this (it holds every row for the
    whole batch): it uses `deliver_all`."""
    box = box or cell_secret_box()
    cur = await conn.execute("select id, kind, name, sealed from app.take_deferred_work(%s)", (limit,))
    sent = 0
    for row in await cur.fetchall():
        sent += await _attempt(conn, mailer, row[:4], box, ROW_LIMIT_SECONDS)
    return sent


async def deliver_all(
    database: Database,
    mailer: Mailer,
    *,
    budget_seconds: float | None = None,
    row_limit_seconds: float | None = None,
    box: SecretBox | None = None,
) -> int:
    """The worker's delivery: one row per short transaction (claimed with skip-locked, sent, outcome written,
    committed), so a slow or failing mail service holds one row and nothing else, and two sweeps never send the same
    row. Stops when the round's time budget is used (the rest waits for the next round); one row is given at most
    `row_limit_seconds`. A failed row waits out its back-off, so the loop moves on to the next. Returns how many
    went."""
    budget = BUDGET_SECONDS if budget_seconds is None else budget_seconds
    limit = ROW_LIMIT_SECONDS if row_limit_seconds is None else row_limit_seconds
    box = box or cell_secret_box()
    started = time.monotonic()
    sent = 0
    while time.monotonic() - started < budget:
        async with database.anonymous() as conn:
            cur = await conn.execute("select id, kind, name, sealed from app.take_deferred_work(1)")
            row = await cur.fetchone()
            if row is None:
                break
            sent += await _attempt(conn, mailer, row[:4], box, limit)
    return sent


async def prune(conn: AsyncConnection) -> int:
    """Delete rows that ended more than a week ago (they hold no payload)."""
    cur = await conn.execute("select app.prune_deferred_work(7)")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


@registry.database_sweep(SWEEP)
async def _sweep(database: Database) -> None:
    from app.core.notifications import default_mailer

    await deliver_all(database, default_mailer())
    async with database.anonymous() as conn:
        await prune(conn)


__all__ = ["SWEEP", "call", "calls", "defer_call", "defer_mail", "deliver", "deliver_all", "prune"]
