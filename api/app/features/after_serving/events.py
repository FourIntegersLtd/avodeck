"""Slice G's `report_served` handler: when a report is served, the pack's deadlines counted from serving are
made at once (once per kind), and when an answers document is served its round and questions are served.

Slice F emits the event after its transaction commits, from a worker thread; the handler runs its own
transaction acting for the case (`Database.job`). Only exception types are logged (rule 6).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anyio.from_thread

from app.core.contracts import events
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

    app = _app
    if app is None or getattr(app.state, "database", None) is None:
        return
    async with app.state.database.job(event.case_id) as conn:
        pack = await pack_for(conn, app.state.packs, event.case_id)
        await ensure_pack_deadlines(conn, pack, event.case_id)
        await conn.execute("select app.after_serving_answers_served(%s, %s)", (event.case_id, event.report_version_id))


@events.events.subscribe(events.ReportServed)
def on_report_served(event: events.ReportServed) -> None:
    try:
        try:
            anyio.from_thread.run(handle_served, event)
        except RuntimeError:  # not in an anyio worker thread: run on this thread's own loop
            asyncio.run(handle_served(event))
    except Exception as exc:  # noqa: BLE001 - the serving itself has already been saved
        logger.warning("report_served handler failed: %s", type(exc).__name__)
