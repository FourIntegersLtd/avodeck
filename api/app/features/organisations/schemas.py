"""Request and response models for slice O. No patient text: organisations, people and codes only."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

OrgName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
OrgRole = Literal["owner", "admin", "expert", "assistant", "viewer"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Policy(BaseModel):
    required_template_id: uuid.UUID | None = None
    max_retention_years: int | None = None


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
