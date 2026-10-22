"""Access dependencies for slice O's routes.

Organisation routes use the core check `require_org_access(action)` (permissions.md "Organisation
actions"). A 🔒 route also lists `STEP_UP_FIRST` in its decorator: FastAPI runs a route's own
dependencies first, so a session without recent MFA is refused with 403 `step_up_required` before
the organisation is looked up. That answer is the same for every organisation, member or not, so it
says nothing about whether one exists; with recent MFA a non-member still gets 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends

from app.core.auth.org_access import OrgAccess, check_org_access, require_org_access
from app.core.auth.step_up import require_step_up
from app.core.http import DbConn

STEP_UP_FIRST: list[Any] = [Depends(require_step_up)]


class _OrgRole:
    """The organisation access check without the action's own step-up, for a route that asks for step-up
    only for part of a change (a team billing profile: only a new tax number, architecture 5.4)."""

    def __init__(self, action: str) -> None:
        self.org_action = action

    async def __call__(self, org_id: uuid.UUID, conn: DbConn) -> OrgAccess:
        return await check_org_access(conn, org_id, self.org_action)


ViewMembers = Annotated[OrgAccess, Depends(require_org_access("org.view_members"))]
ManageMembers = Annotated[OrgAccess, Depends(require_org_access("org.manage_members"))]
SetPolicy = Annotated[OrgAccess, Depends(require_org_access("org.set_policy"))]
Close = Annotated[OrgAccess, Depends(require_org_access("org.close"))]
Billing = Annotated[OrgAccess, Depends(_OrgRole("org.billing"))]
