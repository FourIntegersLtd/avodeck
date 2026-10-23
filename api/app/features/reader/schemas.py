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

    period_start: date | None
    period_end: date | None
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


class RelatedFact(BaseModel):
    """Another record found on the same page for the same day (a visit and its prescription)."""

    fact_id: uuid.UUID
    kind: str
    value: str | None
    event_date: date | None
    status: str


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
    flagged: bool = Field(description="Sent to Points against (a 'points' flag) only.")
    needs_recheck: bool = Field(
        default=False, description="Flagged 'Please check': its page was read again and its words were not found."
    )
    cites_correction: bool = Field(
        default=False, description="Cites words the expert read differently from the computer ('Read by you')."
    )
    related: list[RelatedFact] = Field(
        default_factory=list,
        description="Other suggestions or checked facts on this page for the same day, of another kind.",
    )


class FactList(BaseModel):
    facts: list[FactOut]


class PageWord(BaseModel):
    word_id: str
    text: str
    box: dict[str, float] | None = Field(
        description="The word's box as a share of the page (x0, y0, x1, y1); null for a typed-out word."
    )


class PageWords(BaseModel):
    """The page's words in reading order, for choosing the words a changed fact cites."""

    words: list[PageWord]
    reading: str = Field(
        description="The page's current reading as one text (the expert's corrections in place),"
        " to start a type-out from."
    )


class Progress(BaseModel):
    read: int
    total: int
    seconds_left: int


class CorrectedWordsIn(BaseModel):
    """What some of the cited words really say, when the computer misread them (decision 0018)."""

    word_ids: list[str] = Field(min_length=1, max_length=50)
    text: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _said(self) -> CorrectedWordsIn:
        if not self.text.strip():
            raise ValueError("say what the words say")
        return self


class VerifyIn(BaseModel):
    action: Literal["correct", "not_right", "edit"]
    new_value: str | None = Field(default=None, max_length=2000)
    word_ids: list[str] | None = Field(default=None, min_length=1, max_length=400)
    reason: str | None = Field(default=None, max_length=500)
    fields: dict[str, Any] | None = Field(default=None, max_length=40)
    event_date: date | None = None
    corrected_words: CorrectedWordsIn | None = None


class MoveIn(BaseModel):
    to_page_no: int = Field(gt=0)
    word_ids: list[str] = Field(min_length=1, max_length=400)


class FlagOut(BaseModel):
    fact_id: uuid.UUID
    flagged: bool


Relevance = Literal["relevant", "background", "nothing"]


class UnreadablePage(BaseModel):
    page_no: int
    page_id: uuid.UUID
    source_type: str | None
    handwriting: bool | None
    relevance: str | None
    has_transcription: bool
    needs_clearer_copy: bool
    is_copy: bool = Field(description="Set aside as a copy of another page.")
    has_clearer_copy: bool = Field(description="A clearer copy has been added for it.")
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
