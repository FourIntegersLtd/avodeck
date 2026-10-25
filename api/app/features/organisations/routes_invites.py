"""Invites (architecture 5.4, 10.1): list and send 🔒, cancel 🔒, accept as the invited email."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Path, Request, Response

from app.core.errors import ApiError
from app.core.http import DbConn
from app.features.organisations import service
from app.features.organisations.deps import STEP_UP_FIRST, ManageMembers
from app.features.organisations.router import router
from app.features.organisations.schemas import Invite, InviteAccepted, InviteCreate, InviteList

Token = Annotated[str, Path(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


def _app_origin(request: Request) -> str:
    origin: str | None = request.app.state.app_origin
    if not origin:
        raise ApiError("unavailable", "Invitations cannot be sent yet.", 503)
    return origin


@router.get("/organisations/{org_id}/invites", response_model=InviteList, dependencies=STEP_UP_FIRST)
async def list_invites(org_id: uuid.UUID, conn: DbConn, _: ManageMembers) -> InviteList:
    return await service.invite_list(conn, org_id)


@router.post("/organisations/{org_id}/invites", response_model=Invite, status_code=201, dependencies=STEP_UP_FIRST)
async def send_invite(
    org_id: uuid.UUID, body: InviteCreate, request: Request, conn: DbConn, access: ManageMembers
) -> Invite:
    origin = _app_origin(request)
    return await service.send_invite(conn, request.app.state.mailer, access, origin, body.email, body.role)


@router.delete("/organisations/{org_id}/invites/{invite_id}", status_code=204, dependencies=STEP_UP_FIRST)
async def cancel_invite(org_id: uuid.UUID, invite_id: uuid.UUID, conn: DbConn, _: ManageMembers) -> Response:
    await service.cancel_invite(conn, org_id, invite_id)
    return Response(status_code=204)


@router.post("/invites/{token}/accept", response_model=InviteAccepted)
async def accept_invite(token: Token, conn: DbConn) -> InviteAccepted:
    return await service.accept_invite(conn, token)
