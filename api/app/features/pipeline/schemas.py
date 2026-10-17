"""Request and response bodies for slice A's routes (page numbers and ids only, never page text)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field


class DuplicatePage(BaseModel):
    page_id: uuid.UUID
    page_no: int
    copy_of_page_id: uuid.UUID | None
    copy_of_page_no: int | None
    status: Literal["suggested", "confirmed", "kept"]
    version: int


class Duplicates(BaseModel):
    duplicates: list[DuplicatePage]


class DuplicateReview(BaseModel):
    action: Literal["confirm", "undo"]


class MissingItem(BaseModel):
    id: uuid.UUID
    citing_page_id: uuid.UUID
    citing_page_no: int
    word_ids: list[str]
    description: str
    reference_date: dt.date | None
    status: Literal["open", "requested", "received", "not_available"]
    reason: str | None
    received_page_id: uuid.UUID | None
    received_page_no: int | None
    version: int


class MissingItems(BaseModel):
    missing_items: list[MissingItem]


class MissingItemChange(BaseModel):
    status: Literal["open", "not_available", "received"]
    reason: str | None = Field(default=None, max_length=500)
    page_id: uuid.UUID | None = None
