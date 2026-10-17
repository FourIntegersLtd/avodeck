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


class SourceCount(BaseModel):
    source_type: str | None
    pages: int


class Document(BaseModel):
    id: uuid.UUID
    kind: str
    filename: str
    pages: int | None
    first_page_no: int | None
    last_page_no: int | None
    received_on: dt.date | None
    obtained_how: str | None
    bundle_version: int


class BundleVersion(BaseModel):
    bundle_version: int
    documents: int
    pages: int
    first_page_no: int | None
    last_page_no: int | None
    added_at: dt.datetime


class BundleVersions(BaseModel):
    versions: list[BundleVersion]


class BundleSummary(BaseModel):
    pages: int
    by_source: list[SourceCount]
    duplicates: int
    unreadable: int
    gaps: int
    missing_items: int
    documents: list[Document]
    versions: list[BundleVersion]


class ProvenanceChange(BaseModel):
    received_on: dt.date | None = None
    obtained_how: str | None = Field(default=None, max_length=200)


class AppendRecords(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class Appended(BaseModel):
    job_id: uuid.UUID
    bundle_version: int


class RequestDraftAsk(BaseModel):
    """Nothing to choose yet: the draft lists every open missing item and every unreadable page."""


class RequestDraft(BaseModel):
    label: str
    subject: str
    body: str
    missing_item_ids: list[uuid.UUID]
    clearer_copy_page_ids: list[uuid.UUID]
    clearer_copy_page_nos: list[int]


_ADDRESS = r"^[^@\s,;?&]+@[^@\s,;?&]+\.[^@\s,;?&]+$"


class RequestSent(BaseModel):
    to: str | None = Field(default=None, max_length=320, pattern=_ADDRESS)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    missing_item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    clearer_copy_page_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    follow_up_on: dt.date | None = None


class RequestLogged(BaseModel):
    id: uuid.UUID
    mailto: str
