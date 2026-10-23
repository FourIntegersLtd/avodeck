"""Slice O's case routes (architecture 5.4): change the named expert before serving 🔒, and support access 🔒.

Every route depends on `require_case_access(action)` (rule 9); 🔒 actions carry step-up with it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request, Response

from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.replacement import ReplacementCase, create_replacement_case
from app.core.errors import ApiError
from app.core.http import CurrentSession, DbConn
from app.features.organisations import service, support
from app.features.organisations.router import router
from app.features.organisations.schemas import (
    NamedExpert,
    NamedExpertCandidates,
    NamedExpertChange,
    ReplacementRequest,
    SupportGrant,
    SupportGrantCreate,
    SupportGrantList,
)

Reassign = Annotated[CaseAccess, Depends(require_case_access("case.reassign_named_expert"))]
GrantSupport = Annotated[CaseAccess, Depends(require_case_access("support_access.grant"))]
ViewCase = Annotated[CaseAccess, Depends(require_case_access("case.view_metadata"))]


@router.get("/cases/{case_id}/named-expert/candidates", response_model=NamedExpertCandidates)
async def named_expert_candidates(case_id: uuid.UUID, conn: DbConn, access: ViewCase) -> NamedExpertCandidates:
    """Who may become the named expert. Only those who may reassign see it (no code asked: it only reads)."""
    if "case.reassign_named_expert" not in access.allowed_actions:
        raise ApiError("forbidden", "You cannot do that for this case.", 403)
    return await service.named_expert_candidates(conn, case_id)


@router.post("/cases/{case_id}/named-expert", response_model=NamedExpert)
async def change_named_expert(
    case_id: uuid.UUID, body: NamedExpertChange, request: Request, conn: DbConn, _: Reassign
) -> NamedExpert:
    return await service.reassign(conn, request.app.state.mailer, case_id, body.person_id)


@router.post("/cases/{case_id}/replacement", response_model=ReplacementCase, status_code=201)
async def create_replacement(
    case_id: uuid.UUID, body: ReplacementRequest, conn: DbConn, _: Reassign
) -> ReplacementCase:
    """After serving: a new case for the replacement expert, linked `replaces`; consent to copy recorded."""
    return await create_replacement_case(
        conn, case_id, named_expert_id=body.named_expert_id,
        instructing_party_consented=body.instructing_party_consented,
    )  # fmt: skip


@router.get("/cases/{case_id}/support-access", response_model=SupportGrantList)
async def list_support_access(case_id: uuid.UUID, conn: DbConn, _: ViewCase) -> SupportGrantList:
    return SupportGrantList(grants=await support.grants(conn, case_id))


@router.post("/cases/{case_id}/support-access", response_model=SupportGrant, status_code=201)
async def grant_support_access(
    case_id: uuid.UUID,
    body: SupportGrantCreate,
    request: Request,
    session: CurrentSession,
    conn: DbConn,
    _: GrantSupport,
) -> SupportGrant:
    return await support.give(conn, request.app.state.mailer, case_id, body, session.email)


@router.delete("/cases/{case_id}/support-access/{grant_id}", status_code=204)
async def revoke_support_access(case_id: uuid.UUID, grant_id: uuid.UUID, conn: DbConn, _: GrantSupport) -> Response:
    await support.revoke(conn, case_id, grant_id)
    return Response(status_code=204)
