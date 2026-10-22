"""Slice O's case routes (architecture 5.4): change the named expert before serving 🔒.

Every route depends on `require_case_access(action)` (rule 9); 🔒 actions carry step-up with it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request

from app.core.auth.access import CaseAccess, require_case_access
from app.core.http import DbConn
from app.features.organisations import service
from app.features.organisations.router import router
from app.features.organisations.schemas import NamedExpert, NamedExpertChange

Reassign = Annotated[CaseAccess, Depends(require_case_access("case.reassign_named_expert"))]


@router.post("/cases/{case_id}/named-expert", response_model=NamedExpert)
async def change_named_expert(
    case_id: uuid.UUID, body: NamedExpertChange, request: Request, conn: DbConn, _: Reassign
) -> NamedExpert:
    return await service.reassign(conn, request.app.state.mailer, case_id, body.person_id)
