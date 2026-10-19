"""Slice G's `report_served` handler: when a report is served, the pack's deadlines counted from serving are
made at once (once per kind), and when an answers document is served its round and questions are served.

Slice F emits the event after its transaction commits, from a worker thread, naming who served
(`served_by`). The handler runs its own transaction as that person; an event naming nobody is refused, never
guessed. `CaseWithdrawn` (slice C, in its transaction) cancels the case's open deadlines and adds the count.
Only exception types are logged (rule 6).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anyio.from_thread
from psycopg import AsyncConnection

from app.core.contracts import events
from app.core.db import RequestScope
from app.features.after_serving.common import pack_for

logger = logging.getLogger("app.after_serving")

_app: Any = None


def on_startup(app: Any) -> None:
    global _app
    _app = app


def current_app() -> Any:
    return _app


async def handle_served(event: events.ReportServed) -> None:
    from app.features.after_serving.routes_deadlines import ensure_pack_deadlines

    if event.served_by is None:
        raise ValueError("report_served needs served_by")
    app = _app
    if app is None or getattr(app.state, "database", None) is None:
        return
    database = app.state.database
    async with database.request(RequestScope(event.served_by, (), "report_served")) as conn:
        pack = await pack_for(conn, app.state.packs, event.case_id)
        await ensure_pack_deadlines(conn, pack, event.case_id)
        await conn.execute("select app.after_serving_answers_served(%s, %s)", (event.case_id, event.report_version_id))


@events.events.subscribe_in_transaction(events.CaseWithdrawn)
async def on_case_withdrawn(conn: AsyncConnection, event: events.CaseWithdrawn) -> None:
    cur = await conn.execute("select app.after_serving_cancel_deadlines(%s)", (event.case_id,))
    row = await cur.fetchone()
    event.cancelled.append(int(row[0]) if row else 0)


@events.events.subscribe(events.ReportServed)
def on_report_served(event: events.ReportServed) -> None:
    try:
        try:
            anyio.from_thread.run(handle_served, event)
        except RuntimeError:  # not in an anyio worker thread: run on this thread's own loop
            asyncio.run(handle_served(event))
    except Exception as exc:  # noqa: BLE001 - the serving itself has already been saved
        logger.warning("report_served handler failed: %s", type(exc).__name__)
