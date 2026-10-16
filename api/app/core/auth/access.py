"""`require_case_access(action)`: the only case access check (rule 9, docs/spec/permissions.md).

Answers from `app.case_access()` in the database (the person's case role, organisation role and
the organisation's kind) and the matrix in `permissions.py`. Someone who cannot see the case at
all gets 404, exactly as if it did not exist; someone who can see it but may not do this, 403.
Row-level security repeats the check on every row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request
from psycopg import AsyncConnection

from app.core.auth.permissions import CASE_ACTIONS, allowed_actions
from app.core.auth.session import current_session
from app.core.auth.step_up import check_step_up
from app.core.errors import ApiError
from app.core.http import DbConn


@dataclass(frozen=True)
class CaseAccess:
    case_id: uuid.UUID
    case_role: str | None
    org_role: str | None
    org_kind: str | None
    allowed_actions: tuple[str, ...]


def not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


async def check_case_access(conn: AsyncConnection, case_id: uuid.UUID, action: str) -> CaseAccess:
    if action not in CASE_ACTIONS:
        raise ValueError(f"unknown case action {action!r}: add it to docs/spec/permissions.md first")
    cur = await conn.execute("select case_role, org_role, org_kind from app.case_access(%s)", (case_id,))
    row = await cur.fetchone()
    case_role, org_role, org_kind = row if row is not None else (None, None, None)
    actions = tuple(allowed_actions(case_role, org_role, org_kind))
    if "case.view_metadata" not in actions:
        raise not_found()
    if action not in actions:
        raise ApiError("forbidden", "You cannot do that on this case.", 403)
    return CaseAccess(case_id, case_role, org_role, org_kind, actions)


class CaseAccessDependency:
    """A FastAPI dependency; `case_action` and `step_up` let the route scanners see what a route checks.

    For a 🔒 action (rule 10) it also needs MFA completed in the last 10 minutes, checked after
    access so someone who cannot see the case still gets 404.
    """

    def __init__(self, action: str) -> None:
        if action not in CASE_ACTIONS:
            raise ValueError(f"unknown case action {action!r}: add it to docs/spec/permissions.md first")
        self.case_action = action
        self.step_up = CASE_ACTIONS[action].step_up

    async def __call__(self, request: Request, case_id: uuid.UUID, conn: DbConn) -> CaseAccess:
        access = await check_case_access(conn, case_id, self.case_action)
        if self.step_up:
            check_step_up(current_session(request))
        return access


def require_case_access(action: str) -> CaseAccessDependency:
    return CaseAccessDependency(action)
