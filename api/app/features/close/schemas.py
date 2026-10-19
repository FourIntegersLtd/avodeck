"""Request and response models for closing a case (screen 26). Ids, codes, counts and dates only."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RetentionPolicy = Literal["keep_default", "return_then_delete", "delete_now"]
SHORT_CODE = r"^[a-z][a-z0-9_]{0,39}$"


class Hold(BaseModel):
    id: uuid.UUID
    reason: str
    until: date | None
    set_by: uuid.UUID
    created_at: datetime
    released_at: datetime | None


class CloseSummary(BaseModel):
    """What screen 26 shows: where the case is, when it will be deleted, and what is holding it."""

    case_id: uuid.UUID
    status: str
    settled_at: datetime | None
    retention_policy: str | None
    delete_after: date | None
    retention_years: int
    ever_served: bool
    holds: list[Hold]
    held: bool
    deletion_started: bool
    deleted_at: datetime | None
    job_id: uuid.UUID | None = None


class RetentionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: RetentionPolicy


class HoldCreate(BaseModel):
    """A short reason code (never free text) and an optional end date, today or later."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(pattern=SHORT_CODE)
    until: date | None = None

    @field_validator("until")
    @classmethod
    def _not_past(cls, value: date | None) -> date | None:
        if value is not None and value < date.today():
            raise ValueError("the end date has passed")
        return value


class AuditEntry(BaseModel):
    seq: int
    at: datetime
    action: str
    target: str
    person_id: uuid.UUID | None


class AuditTrail(BaseModel):
    entries: list[AuditEntry]


class Certificate(BaseModel):
    """Proof of deletion (rule 6): counts, hashes and dates, never patient text or names."""

    case_id: uuid.UUID
    deleted_at: datetime
    counts: dict[str, Any]
    hashes: dict[str, Any]
    log_chain_heads: dict[str, Any]
    backup_expiry_on: date | None
    sent_to: list[str]


ExportKind = Literal["reports", "method", "reads", "invoice"]
EXPORT_KINDS: tuple[ExportKind, ...] = ("reports", "method", "reads", "invoice")
# Ticked when the screen opens: the invoice is the expert's own paperwork, so it is opt-in.
EXPORT_KINDS_BY_DEFAULT: frozenset[str] = frozenset({"reports", "method", "reads"})


class ExportRequest(BaseModel):
    """Which kinds of file to build; leave out `files` for all of them."""

    model_config = ConfigDict(extra="forbid")

    files: list[ExportKind] | None = Field(default=None, min_length=1, max_length=len(EXPORT_KINDS))

    @field_validator("files")
    @classmethod
    def _once_each(cls, value: list[ExportKind] | None) -> list[ExportKind] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("each kind once")
        return value


class ExportFile(BaseModel):
    kind: ExportKind
    count: int  # how many items the kind holds (report versions, page reads, invoices, ...)
    selected: bool  # ticked by default


class ExportFiles(BaseModel):
    files: list[ExportFile]


class JobQueued(BaseModel):
    job_id: uuid.UUID
