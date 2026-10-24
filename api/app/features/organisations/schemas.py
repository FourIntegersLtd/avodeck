"""Request and response models for slice O. No patient text: organisations, people and codes only."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

OrgName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
OrgRole = Literal["owner", "admin", "expert", "assistant", "viewer"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Policy(BaseModel):
    required_template_id: uuid.UUID | None = None
    max_retention_years: int | None = None


class PolicyUpdate(Strict):
    """The whole policy: a key left out is cleared (the person's own default applies)."""

    required_template_id: uuid.UUID | None = None
    max_retention_years: Annotated[int, Field(strict=True, ge=1, le=100)] | None = None


class Organisation(BaseModel):
    id: uuid.UUID
    name: str
    kind: Literal["personal", "team"]
    region: str
    role: OrgRole
    policy: Policy
    created_at: datetime
    allowed_actions: list[str] = []  # organisation actions this person may do here (permissions.md)
    assignable_roles: list[OrgRole] = []  # the roles this person may give


class OrganisationList(BaseModel):
    organisations: list[Organisation]


class OrganisationCreate(Strict):
    name: OrgName


class OrganisationRename(Strict):
    name: OrgName


class OrgMember(BaseModel):
    membership_id: uuid.UUID
    person_id: uuid.UUID
    full_name: str | None
    email: str
    role: OrgRole
    started_at: datetime
    ended_at: datetime | None
    case_count: int | None = None  # owners and admins only; null for everyone else


class OrgMemberList(BaseModel):
    members: list[OrgMember]


class RoleChange(Strict):
    role: OrgRole


class NamedExpertChange(Strict):
    person_id: uuid.UUID


class ReplacementRequest(Strict):
    named_expert_id: uuid.UUID
    instructing_party_consented: bool


class NamedExpert(BaseModel):
    case_id: uuid.UUID
    named_expert_id: uuid.UUID


Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class TeamBillingProfileIn(Strict):
    legal_name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=1000)
    tax_number: str | None = Field(default=None, max_length=60)
    tax_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)] = Decimal(0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    hourly_rate: Money | None = None
    invoice_prefix: str = Field(default="", max_length=20)


class TeamBillingProfile(TeamBillingProfileIn):
    id: uuid.UUID


class TeamBillingProfileOut(BaseModel):
    profile: TeamBillingProfile | None


Email = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
]
InviteStatus = Literal["pending", "accepted", "cancelled", "expired"]


class InviteCreate(Strict):
    email: Email
    role: OrgRole


class Invite(BaseModel):
    id: uuid.UUID
    email: str
    role: OrgRole
    status: InviteStatus
    invited_by: uuid.UUID
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None


class InviteList(BaseModel):
    invites: list[Invite]


class InviteAccepted(BaseModel):
    organisation_id: uuid.UUID
    role: OrgRole
    membership_id: uuid.UUID


ReasonCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]


class SupportGrantCreate(Strict):
    reason: ReasonCode
    granted_to: uuid.UUID | None = None


class SupportGrant(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    granted_by: uuid.UUID
    granted_to: uuid.UUID | None
    reason: str
    granted_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    active: bool


class SupportGrantList(BaseModel):
    grants: list[SupportGrant]


# Support answers carry no free text (rule 6; tests/guards/test_organisations_support_rule6.py): every string
# is an enum, an id, a date or a code held to this pattern.
SupportCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]
CaseStatus = Literal["draft", "processing", "active", "served", "settled", "withdrawn", "archived", "deleted"]
JobStatus = Literal["queued", "running", "done", "failed", "paused"]


class SupportCase(BaseModel):
    """What the support console shows of a case: metadata only (permissions.md "Avodeck staff")."""

    case_id: uuid.UUID
    status: SupportCode
    organisation_id: uuid.UUID
    organisation_kind: Literal["personal", "team"]
    created_at: datetime
    served: bool
    grant_active: bool
    grant_expires_at: datetime | None


class SupportCaseList(BaseModel):
    cases: list[SupportCase]


class SupportCaseGrant(BaseModel):
    id: uuid.UUID
    granted_to: uuid.UUID | None
    reason: SupportCode
    granted_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    active: bool


class SupportJob(BaseModel):
    id: uuid.UUID
    kind: SupportCode | None
    status: JobStatus
    attempts: int
    error_code: SupportCode | None
    step: SupportCode | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class SupportPages(BaseModel):
    total: int
    unread_by_named_expert: int
    unreadable: int
    needs_clearer_copy: int
    quarantined: int
    superseded: int


class SupportFacts(BaseModel):
    suggested: int
    verified: int  # checked by the named expert: verified or edited
    needs_recheck: int


class SupportInstructionVersion(BaseModel):
    n: int
    read_status: Literal["not_needed", "reading", "read", "failed"]
    job_error_code: SupportCode | None


class SupportQuestions(BaseModel):
    state: Literal["none", "to_check", "confirmed"]
    instruction_versions: list[SupportInstructionVersion]


class SupportFinalCheck(BaseModel):
    status: Literal["not_run", "passed", "failed"]
    item_codes: list[SupportCode]


class SupportReport(BaseModel):
    latest_version_status: Literal["draft", "locked", "served", "withdrawn"] | None
    locked_at: datetime | None
    served_at: datetime | None
    final_check: SupportFinalCheck
    declaration_acknowledged: bool
    signature_present: bool
    signature_readable: bool  # its record is complete; the console never opens the image
    pack_correction_pending: bool


class SupportHold(BaseModel):
    present: bool
    until: date | None
    reason_code: SupportCode | None


class SupportExport(BaseModel):
    status: Literal["none", "queued", "running", "done", "failed", "paused"]
    downloaded: bool


class SupportClose(BaseModel):
    status: CaseStatus
    delete_after: date | None
    hold: SupportHold
    deletion_started: bool
    export: SupportExport


class SupportOfflinePackage(BaseModel):
    id: uuid.UUID
    state: Literal["built", "downloaded", "revoked"]
    expires_at: datetime | None
    stale_reason: SupportCode | None
    downloaded_at: datetime | None


class SupportNamedExpertChange(BaseModel):
    changed_at: datetime


class SupportDeadlines(BaseModel):
    open: int
    overdue: int
    done: int
    cancelled: int


class SupportCaseDetail(BaseModel):
    """One case for the console: its grants, then where it is stuck, in counts, codes and dates only."""

    case: SupportCase
    grants: list[SupportCaseGrant]
    content_access: bool
    jobs: list[SupportJob]
    pages: SupportPages
    facts: SupportFacts
    questions: SupportQuestions
    report: SupportReport
    close: SupportClose
    offline_packages: list[SupportOfflinePackage]
    named_expert_history: list[SupportNamedExpertChange]
    deadlines: SupportDeadlines


class SupportPerson(BaseModel):
    """An account's state for a support call: flags, counts, dates and ids, never a name or an email."""

    person_id: uuid.UUID
    created_at: datetime
    closed_at: datetime | None
    mfa_enrolled: bool | None  # null: never signed in, so not known
    mfa_locked_until: datetime | None
    recovery_codes_left: int
    last_mfa_at: datetime | None
    pending_email_change: bool
    custodian_set: bool
    blocking_case_ids_for_closure: list[uuid.UUID]


class SupportPersonList(BaseModel):
    people: list[SupportPerson]


class SupportMember(BaseModel):
    person_id: uuid.UUID
    role: OrgRole
    started_at: datetime
    ended_at: datetime | None


class SupportInvite(BaseModel):
    """An invite's state; never its token or the invitee's email."""

    id: uuid.UUID
    role: OrgRole
    state: Literal["pending", "accepted", "cancelled", "expired"]
    sent_at: datetime
    expires_at: datetime


class SupportReassignment(BaseModel):
    case_id: uuid.UUID
    changed_at: datetime


class SupportOrganisation(BaseModel):
    """An organisation for the console: never its name."""

    organisation_id: uuid.UUID
    kind: Literal["personal", "team"]
    created_at: datetime
    closed_at: datetime | None
    owner_count: int
    open_case_count: int
    members: list[SupportMember]
    invites: list[SupportInvite]
    reassignments: list[SupportReassignment]


class SupportCellStatus(BaseModel):
    """How the cell is running. `speech_ok`, `key_service_ok`: set up in this cell."""

    speech_ok: bool
    key_service_ok: bool
    worker_heartbeat_age_seconds: int | None
    queue_depth: int
    paused_jobs: int


class CustodyTransferCreate(Strict):
    case_id: uuid.UUID
    to: Literal["custodian", "organisation"]
    evidence_note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CustodyTransfer(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    to_person_id: uuid.UUID | None
    to_organisation_id: uuid.UUID | None
    transferred_at: datetime


class AccountClosed(BaseModel):
    closed_at: datetime
    deletion_job_ids: list[uuid.UUID]


class Custodian(Strict):
    """A records custodian: both a name and an email, or neither (cleared)."""

    name: OrgName | None
    email: Email | None

    @model_validator(mode="after")
    def _both_or_neither(self) -> Custodian:
        if (self.name is None) != (self.email is None):
            raise ValueError("a custodian has a name and an email, or neither")
        return self


class NamedExpertCandidate(BaseModel):
    person_id: uuid.UUID
    full_name: str | None
    email: str
    role: OrgRole


class NamedExpertCandidates(BaseModel):
    """Who may become the case's named expert; `served` says the expert can no longer change."""

    served: bool
    candidates: list[NamedExpertCandidate]


class CloseAccountCase(BaseModel):
    case_id: uuid.UUID
    label: str
    exported: bool


class CloseAccountPreview(BaseModel):
    """The cases closing the account would delete, each with whether its export is done."""

    cases: list[CloseAccountCase]
