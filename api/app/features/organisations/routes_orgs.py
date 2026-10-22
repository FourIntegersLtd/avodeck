"""Organisations (architecture 5.4): list mine, create a team, view one, rename it."""

from __future__ import annotations

import uuid

from fastapi import Request

from app.core.http import DbConn
from app.features.organisations import service
from app.features.organisations.deps import STEP_UP_FIRST, org_access
from app.features.organisations.router import router
from app.features.organisations.schemas import (
    Organisation,
    OrganisationCreate,
    OrganisationList,
    OrganisationRename,
)

ViewMembers = org_access("org.view_members")
SetPolicy = org_access("org.set_policy")


@router.get("/organisations", response_model=OrganisationList)
async def list_organisations(conn: DbConn) -> OrganisationList:
    return OrganisationList(organisations=await service.organisations(conn))


@router.post("/organisations", response_model=Organisation, status_code=201)
async def create_organisation(body: OrganisationCreate, request: Request, conn: DbConn) -> Organisation:
    region: str = request.app.state.region
    return await service.create_team(conn, body.name, region)


@router.get("/organisations/{org_id}", response_model=Organisation)
async def get_organisation(org_id: uuid.UUID, conn: DbConn, _: ViewMembers) -> Organisation:
    return await service.organisation(conn, org_id)


@router.patch("/organisations/{org_id}", response_model=Organisation, dependencies=STEP_UP_FIRST)
async def rename_organisation(org_id: uuid.UUID, body: OrganisationRename, conn: DbConn, _: SetPolicy) -> Organisation:
    return await service.rename(conn, org_id, body.name)
