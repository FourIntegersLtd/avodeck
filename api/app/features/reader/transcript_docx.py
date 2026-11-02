"""The Word copy of the records transcript (ADR 0035): the layout as headings and paragraphs, no pictures."""

from __future__ import annotations

from datetime import date

from app.features.reader.transcript_layout import Layout


def build(layout: Layout, *, date_order: str, language: str, received: dict[int, date]) -> bytes:
    """`received` maps a page number to the date its letter was received, for letters only."""
    raise NotImplementedError
