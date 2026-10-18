"""Request and response shapes for the reader routes (screens S09 and S10). Page numbers and ids only in errors."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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


class VerifyIn(BaseModel):
    action: Literal["correct", "not_right", "edit"]
    new_value: str | None = Field(default=None, max_length=2000)
    word_ids: list[str] | None = Field(default=None, min_length=1, max_length=400)
    reason: str | None = Field(default=None, max_length=500)


class MoveIn(BaseModel):
    to_page_no: int = Field(gt=0)
    word_ids: list[str] = Field(min_length=1, max_length=400)


class FlagOut(BaseModel):
    fact_id: uuid.UUID
    flagged: bool


Relevance = Literal["relevant", "background"]


class UnreadablePage(BaseModel):
    page_no: int
    page_id: uuid.UUID
    source_type: str | None
    handwriting: bool | None
    relevance: str | None
    has_transcription: bool
    needs_clearer_copy: bool
    done: bool


class UnreadableQueue(BaseModel):
    pages: list[UnreadablePage]
    done: int
    total: int


class TranscriptionIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    relevance: Relevance | None = None


class PageChange(BaseModel):
    relevance: Relevance | None = None
    needs_clearer_copy: bool | None = None

    @model_validator(mode="after")
    def _something(self) -> PageChange:
        if not self.model_fields_set:
            raise ValueError("nothing to change")
        return self


class RotateIn(BaseModel):
    degrees: Literal[90, 180, 270]


class JobOut(BaseModel):
    job_id: uuid.UUID
