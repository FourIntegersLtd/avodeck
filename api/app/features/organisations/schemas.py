"""Request and response models for slice O. No patient text: organisations, people and codes only."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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


class Member(BaseModel):
    membership_id: uuid.UUID
    person_id: uuid.UUID
    full_name: str | None
    email: str
    role: OrgRole
    started_at: datetime
    ended_at: datetime | None


class MemberList(BaseModel):
    members: list[Member]


class RoleChange(Strict):
    role: OrgRole


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
