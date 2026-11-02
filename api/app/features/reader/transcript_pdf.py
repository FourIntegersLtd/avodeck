"""The PDF copy of the records transcript (ADR 0035): the layout as text, then a picture of every page at the back.

Each entry's page number links to that page's picture; sections and the pictures part are bookmarked. Pictures are
held in memory only and never written to disk (ADR 0021).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from app.features.reader.transcript_layout import Layout

PICTURE_EDGE = 1400
PICTURE_QUALITY = 60


def build_pdf(
    layout: Layout, pictures: Iterable[tuple[int, bytes]], *, date_order: str, language: str, received: dict[int, date]
) -> bytes:
    """`pictures` are (page number, image bytes) in page order; a page with no picture shows a "not available" line."""
    raise NotImplementedError
