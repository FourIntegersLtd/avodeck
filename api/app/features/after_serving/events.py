"""Slice G's `report_served` handler: when a report is served, the pack's deadlines counted from serving are
made at once (once per kind), and when an answers document is served its round and questions are served.

Slice F publishes the event inside the serve transaction (`events.publish`), naming who served (`served_by`).
The handler runs on that connection, as that person under row-level security, so its work commits with the serving
and a failure undoes both (BG-16); an event naming nobody is refused, never guessed. `CaseWithdrawn` (slice C, in
its transaction) cancels the case's open deadlines and adds the count.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.core.contracts import events
from app.features.after_serving.common import pack_for

_app: Any = None


def on_startup(app: Any) -> None:
    global _app
    _app = app


def current_app() -> Any:
    return _app


async def handle_served(conn: AsyncConnection, event: events.ReportServed) -> None:
    from app.features.after_serving.routes_deadlines import ensure_pack_deadlines

    if event.served_by is None:
        raise ValueError("report_served needs served_by")
    app = _app
    if app is None:
        raise RuntimeError("after_serving is not started")
    pack = await pack_for(conn, app.state.packs, event.case_id)
    await ensure_pack_deadlines(conn, pack, event.case_id)
    await conn.execute("select app.after_serving_answers_served(%s, %s)", (event.case_id, event.report_version_id))


@events.events.subscribe_in_transaction(events.CaseWithdrawn)
async def on_case_withdrawn(conn: AsyncConnection, event: events.CaseWithdrawn) -> None:
    cur = await conn.execute("select app.after_serving_cancel_deadlines(%s)", (event.case_id,))
    row = await cur.fetchone()
    event.cancelled.append(int(row[0]) if row else 0)


@events.events.subscribe_in_transaction(events.ReportServed)
async def on_report_served(conn: AsyncConnection, event: events.ReportServed) -> None:
    await handle_served(conn, event)
