"""Organisations (architecture 5.4): list mine, create a team, view one, rename it."""

from __future__ import annotations

import uuid

from fastapi import Request, Response

from app.core.http import DbConn
from app.features.organisations import service
from app.features.organisations.deps import STEP_UP_FIRST, Close, SetPolicy, ViewMembers
from app.features.organisations.router import router
from app.features.organisations.schemas import (
    Organisation,
    OrganisationCreate,
    OrganisationList,
    OrganisationRename,
    PolicyUpdate,
)


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


@router.put("/organisations/{org_id}/policy", response_model=Organisation, dependencies=STEP_UP_FIRST)
async def set_policy(org_id: uuid.UUID, body: PolicyUpdate, conn: DbConn, _: SetPolicy) -> Organisation:
    return await service.set_policy(conn, org_id, body)


@router.post("/organisations/{org_id}/close", status_code=204, dependencies=STEP_UP_FIRST)
async def close_organisation(org_id: uuid.UUID, conn: DbConn, _: Close) -> Response:
    await service.close_organisation(conn, org_id)
    return Response(status_code=204)


@router.patch("/organisations/{org_id}", response_model=Organisation, dependencies=STEP_UP_FIRST)
async def rename_organisation(org_id: uuid.UUID, body: OrganisationRename, conn: DbConn, _: SetPolicy) -> Organisation:
    return await service.rename(conn, org_id, body.name)
