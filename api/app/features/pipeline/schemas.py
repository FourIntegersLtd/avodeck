"""Request and response bodies for slice A's routes (page numbers and ids only, never page text)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer


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
    media_type: str | None = None  # what the file turned out to be (F36)
    part_of: uuid.UUID | None = None  # the email or zip it came out of

    @model_serializer(mode="wrap")
    def _omit_unknown(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """`media_type` and `part_of` appear once the file has been sniffed (when it is cut into pages)."""
        out: dict[str, Any] = handler(self)
        if out.get("media_type") is None:
            out.pop("media_type", None)
            out.pop("part_of", None)
        return out


class Exhibit(BaseModel):
    """A file Avodeck keeps and lists but cannot read: audio, video, imaging. Never paged."""

    id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    media_type: str
    size: int
    part_of: uuid.UUID | None = None
    added_at: dt.datetime


class Exhibits(BaseModel):
    exhibits: list[Exhibit]


class BundleVersion(BaseModel):
    bundle_version: int
    documents: int
    pages: int
    first_page_no: int | None
    last_page_no: int | None
    added_at: dt.datetime


class BundleVersions(BaseModel):
    versions: list[BundleVersion]


QuarantineStatus = Literal["suggested", "confirmed", "dismissed", "undone", "purged"]


class QuarantinePage(BaseModel):
    page_id: uuid.UUID
    page_no: int
    status: QuarantineStatus
    reason: str  # a short code; the screen shows its words
    job_id: uuid.UUID | None = None  # reading the page again, after an undo


class QuarantineAsk(BaseModel):
    reason: str = Field(pattern=r"^[a-z_]{1,40}$")


class ForeignPage(BaseModel):
    page_id: uuid.UUID
    page_no: int
    language: str  # a language code; the screen shows its name


class BundleSummary(BaseModel):
    pages: int
    by_source: list[SourceCount]
    duplicates: int
    unreadable: int
    gaps: int
    missing_items: int
    documents: list[Document]
    versions: list[BundleVersion]
    quarantine_suggestions: list[QuarantinePage] = []  # pages that may be someone else's, never hidden
    quarantined: list[QuarantinePage] = []  # pages the named expert removed; they keep their numbers
    exhibits: list[Exhibit] = []  # files kept but not readable by Avodeck
    foreign_language: list[ForeignPage] = []  # pages not in the pack's language; never translated


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
    translation_page_ids: list[uuid.UUID] = []  # pages to ask a certified translation of (F37)
    translation_page_nos: list[int] = []


_ADDRESS = r"^[^@\s,;?&]+@[^@\s,;?&]+\.[^@\s,;?&]+$"


class RequestSent(BaseModel):
    to: str | None = Field(default=None, max_length=320, pattern=_ADDRESS)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    missing_item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    clearer_copy_page_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    quarantined_page_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)  # pages it tells them about
    follow_up_on: dt.date | None = None


class RequestLogged(BaseModel):
    id: uuid.UUID
    mailto: str


class JobQueued(BaseModel):
    job_id: uuid.UUID


class ProcessingJob(BaseModel):
    """A processing job's progress: status, step code, percentage, time left, error code (rule 6)."""

    id: uuid.UUID
    kind: str
    status: str
    step: str | None
    pct: int | None
    eta_seconds: int | None
    error_code: str | None


class Processing(BaseModel):
    job: ProcessingJob | None  # the case's latest processing job; none before the first upload


class ProcessingCancelled(BaseModel):
    cancelled: bool  # false when nothing was processing (or a stop was already asked for)


class ReplaceAsk(BaseModel):
    document_id: uuid.UUID  # the clearer copy, uploaded as later records


class PageReplacement(BaseModel):
    page_id: uuid.UUID
    document_id: uuid.UUID
    new_page_id: uuid.UUID | None  # set once the copy has been cut into pages
    new_page_no: int | None
