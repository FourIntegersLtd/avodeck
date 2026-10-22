"""Access dependencies for slice O's routes.

Organisation routes use the core check `require_org_access(action)` (permissions.md "Organisation
actions"). A 🔒 route also lists `STEP_UP_FIRST` in its decorator: FastAPI runs a route's own
dependencies first, so a session without recent MFA is refused with 403 `step_up_required` before
the organisation is looked up. That answer is the same for every organisation, member or not, so it
says nothing about whether one exists; with recent MFA a non-member still gets 404.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends

from app.core.auth.org_access import OrgAccess, require_org_access
from app.core.auth.step_up import require_step_up

STEP_UP_FIRST: list[Any] = [Depends(require_step_up)]


def org_access(action: str) -> Any:
    return Annotated[OrgAccess, Depends(require_org_access(action))]
