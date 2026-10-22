"""`require_org_access(action)`: the only organisation access check (rule 9, docs/spec/permissions.md).

The table is `ORG_ACTIONS` in `permissions.py`. Someone who is not a current member of the
organisation (never joined, left, or whose account is closed), or asks about one that does not
exist, gets 404; a member whose role lacks the action, 403. For a 🔒 action it also needs MFA
completed in the last 10 minutes, checked after access so a non-member still gets 404. Row-level
security on `organisations` and `memberships` (migration 0003) repeats the membership check.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request
from psycopg import AsyncConnection

from app.core.auth.access import not_found
from app.core.auth.permissions import ORG_ACTIONS, org_allowed_actions
from app.core.auth.session import current_session
from app.core.auth.step_up import check_step_up
from app.core.errors import ApiError
from app.core.http import DbConn


@dataclass(frozen=True)
class OrgAccess:
    org_id: uuid.UUID
    role: str
    kind: str
    allowed_actions: tuple[str, ...]


def _known(action: str) -> None:
    if action not in ORG_ACTIONS:
        raise ValueError(f"unknown organisation action {action!r}: add it to docs/spec/permissions.md first")


async def check_org_access(conn: AsyncConnection, org_id: uuid.UUID, action: str) -> OrgAccess:
    _known(action)
    cur = await conn.execute(
        """
        select m.role, o.kind
        from app.memberships m
        join app.organisations o on o.id = m.organisation_id
        join app.people p on p.id = m.person_id
        where m.organisation_id = %s and m.person_id = app.current_person_id() and m.ended_at is null
          and p.closed_at is null
        """,
        (org_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    role, kind = row
    actions = tuple(org_allowed_actions(role))
    if action not in actions:
        raise ApiError("forbidden", "You cannot do that for this organisation.", 403)
    return OrgAccess(org_id, role, kind, actions)


class OrgAccessDependency:
    """A FastAPI dependency; `org_action` and `step_up` let the route scanners see what a route checks."""

    def __init__(self, action: str) -> None:
        _known(action)
        self.org_action = action
        self.step_up = ORG_ACTIONS[action].step_up

    async def __call__(self, request: Request, org_id: uuid.UUID, conn: DbConn) -> OrgAccess:
        access = await check_org_access(conn, org_id, self.org_action)
        if self.step_up:
            check_step_up(current_session(request))
        return access


def require_org_access(action: str) -> OrgAccessDependency:
    return OrgAccessDependency(action)
