"""Request and response models for slice O. No patient text: organisations, people and codes only."""

from __future__ import annotations

import uuid
from datetime import datetime
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


class OrgMemberList(BaseModel):
    members: list[OrgMember]


class RoleChange(Strict):
    role: OrgRole


class NamedExpertChange(Strict):
    person_id: uuid.UUID


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


class Custodian(Strict):
    """A records custodian: both a name and an email, or neither (cleared)."""

    name: OrgName | None
    email: Email | None

    @model_validator(mode="after")
    def _both_or_neither(self) -> Custodian:
        if (self.name is None) != (self.email is None):
            raise ValueError("a custodian has a name and an email, or neither")
        return self
