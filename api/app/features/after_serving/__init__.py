"""Slice G, after serving: deadlines and court directions, written questions, new records, the other
expert's report and supplementary reports (screens 19, 20, 21, 21b and S29).

`router` (built in `router.py`) is mounted by `app.main.create_app()` with `mount()`. `on_startup` keeps the
app so the `report_served` handler can reach its database. Importing the router loads every route module,
job kind, Today provider and event handler in one order.
"""

from __future__ import annotations

from app.features.after_serving.events import on_startup
from app.features.after_serving.router import router

__all__ = ["on_startup", "router"]
