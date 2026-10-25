"""Members (architecture 5.4): list, change a role 🔒, end a membership 🔒 (F32: the last owner stays)."""

from __future__ import annotations

import uuid

from fastapi import Response

from app.core.http import DbConn
from app.features.organisations import service
from app.features.organisations.deps import STEP_UP_FIRST, ManageMembers, ViewMembers
from app.features.organisations.router import router
from app.features.organisations.schemas import OrgMember, OrgMemberList, RoleChange


@router.get("/organisations/{org_id}/members", response_model=OrgMemberList)
async def list_members(org_id: uuid.UUID, conn: DbConn, _: ViewMembers) -> OrgMemberList:
    return await service.member_list(conn, org_id)


@router.patch("/organisations/{org_id}/members/{membership_id}", response_model=OrgMember, dependencies=STEP_UP_FIRST)
async def change_member_role(
    org_id: uuid.UUID, membership_id: uuid.UUID, body: RoleChange, conn: DbConn, access: ManageMembers
) -> OrgMember:
    return await service.change_role(conn, access, membership_id, body.role)


@router.post("/organisations/{org_id}/leave", status_code=204)
async def leave_organisation(org_id: uuid.UUID, conn: DbConn, _: ViewMembers) -> Response:
    await service.leave(conn, org_id)
    return Response(status_code=204)


@router.delete("/organisations/{org_id}/members/{membership_id}", status_code=204, dependencies=STEP_UP_FIRST)
async def remove_member(org_id: uuid.UUID, membership_id: uuid.UUID, conn: DbConn, access: ManageMembers) -> Response:
    await service.end_membership(conn, access, membership_id)
    return Response(status_code=204)
