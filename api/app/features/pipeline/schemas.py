"""Request and response bodies for slice A's routes (page numbers and ids only, never page text)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel


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
