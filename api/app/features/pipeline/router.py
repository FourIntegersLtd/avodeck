"""Slice A's routes (architecture 5.2): bundle summary, duplicates, thumbnails, missing items."""

from __future__ import annotations

from app.core.http import router

pipeline_router = router(tags=["pipeline"])
