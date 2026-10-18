"""Request and response shapes for the reader routes (screens S09 and S10). Page numbers and ids only in errors."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class PageSummary(BaseModel):
    page_no: int
    page_id: uuid.UUID
    source_type: str | None
    read: bool
    in_period: bool | None
    unreadable: bool
    duplicate_of_page_no: int | None


class PageList(BaseModel):
    pages: list[PageSummary]
    next_cursor: int | None


class PageDetail(PageSummary):
    """One page. Never carries facts: they come from `/facts`, behind the blind-read gate (rule 3)."""

    rotation: int
    relevance: str | None
    needs_clearer_copy: bool
    superseded_by_page_no: int | None
    handwriting: bool | None
    has_transcription: bool
    first_read_at: datetime | None


class ReadIn(BaseModel):
    dwell_seconds: int = Field(ge=0, le=86_400)


class ReadOut(BaseModel):
    page_no: int
    read: bool
    first_read_at: datetime


class FactOut(BaseModel):
    id: uuid.UUID
    kind: str
    fields: dict[str, Any]
    value: str | None
    word_ids: list[str]
    bbox: dict[str, Any] | None
    event_date: date | None
    status: str
    version: int
    flagged: bool


class FactList(BaseModel):
    facts: list[FactOut]


class Progress(BaseModel):
    read: int
    total: int
    seconds_left: int
