"""`require_case_access(action)`: the only case access check (rule 9, docs/spec/permissions.md).

Answers from `app.case_access()` in the database (the person's case role, organisation role and
the organisation's kind) and the matrix in `permissions.py`. Someone who cannot see the case at
all gets 404, exactly as if it did not exist; someone who can see it but may not do this, 403.
Row-level security repeats the check on every row. A deleted case answers only
`DELETED_CASE_ACTIONS` (its certificate of deletion); everything else on it is 404.

Two rules on the person (migration 0030):
- Avodeck support (`support` role, a live grant from the named expert) only reads; every request
  under it is written to the case's audit trail as `support_access.viewed` and needs MFA in the
  last 10 minutes (`aal2`) on every call.
- A closed account (S15) takes only `CLOSED_ACCOUNT_ACTIONS`, and only as the named expert of a case
  that has been served; anything else is 404.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from fastapi import Request, WebSocket, WebSocketException
from psycopg import AsyncConnection

from app.core import audit
from app.core.auth import session as sessions
from app.core.auth.permissions import CASE_ACTIONS, SUPPORT_ROLE, allowed_actions
from app.core.auth.session import current_session
from app.core.auth.step_up import check_step_up
from app.core.db import RequestScope
from app.core.errors import ApiError
from app.core.http import DbConn


@dataclass(frozen=True)
class CaseAccess:
    case_id: uuid.UUID
    case_role: str | None
    org_role: str | None
    org_kind: str | None
    allowed_actions: tuple[str, ...]


# The only actions a deleted case still answers, for the people the matrix allows; anything else is 404.
DELETED_CASE_ACTIONS = frozenset({"case.view_certificate"})
# All a closed account may still do, on the served cases it is the named expert of (the same list
# is in `app.case_access()`, migration 0030).
CLOSED_ACCOUNT_ACTIONS = frozenset(
    {"case.view_metadata", "case.view_content", "case.view_audit", "case.view_certificate"}
)
SUPPORT_VIEWED = "support_access.viewed"


def not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


async def check_case_access(conn: AsyncConnection, case_id: uuid.UUID, action: str) -> CaseAccess:
    if action not in CASE_ACTIONS:
        raise ValueError(f"unknown case action {action!r}: add it to docs/spec/permissions.md first")
    cur = await conn.execute(
        "select case_role, org_role, org_kind, deleted, closed from app.case_access(%s, %s)", (case_id, action)
    )
    row = await cur.fetchone()
    case_role, org_role, org_kind, deleted, closed = row if row is not None else (None, None, None, False, False)
    actions = tuple(allowed_actions(case_role, org_role, org_kind))
    if closed:
        # A closed account only reads; anything else looks like a case it cannot see.
        actions = tuple(a for a in actions if a in CLOSED_ACCOUNT_ACTIONS)
        if action not in actions:
            raise not_found()
    if deleted:
        # A deleted case (the tombstone purge_case leaves) answers its certificate and nothing else.
        if action not in DELETED_CASE_ACTIONS or action not in actions:
            raise not_found()
        return CaseAccess(case_id, case_role, org_role, org_kind, (action,))
    if "case.view_metadata" not in actions:
        raise not_found()
    if action not in actions:
        raise ApiError("forbidden", "You cannot do that on this case.", 403)
    if case_role == SUPPORT_ROLE:
        await audit.record(conn, action=SUPPORT_VIEWED, target=f"case:{case_id}", case_id=case_id)
    return CaseAccess(case_id, case_role, org_role, org_kind, actions)


class CaseAccessDependency:
    """A FastAPI dependency; `case_action` and `step_up` let the route scanners see what a route checks.

    For a 🔒 action (rule 10), and for every request under the support role, it also needs MFA
    completed in the last 10 minutes, checked after access so someone who cannot see the case
    still gets 404.
    """

    def __init__(self, action: str) -> None:
        if action not in CASE_ACTIONS:
            raise ValueError(f"unknown case action {action!r}: add it to docs/spec/permissions.md first")
        self.case_action = action
        self.step_up = CASE_ACTIONS[action].step_up

    async def __call__(self, request: Request, case_id: uuid.UUID, conn: DbConn) -> CaseAccess:
        access = await check_case_access(conn, case_id, self.case_action)
        if self.step_up or access.case_role == SUPPORT_ROLE:
            check_step_up(current_session(request))
        return access


def require_case_access(action: str) -> CaseAccessDependency:
    return CaseAccessDependency(action)


# --- WebSocket routes ------------------------------------------------------------------------------
# A WebSocket cannot answer with an HTTP status, so refusals close it before it is accepted with
# 4000 + the status: 4401 not signed in, 4403 refused (origin, MFA, role, step-up), 4404 no access,
# 4422 a query value that is not a short code. Browsers always send Origin on a WebSocket, so it is
# required here (there is no CSRF header; the origin check is what stops other sites).

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


@dataclass(frozen=True)
class SocketCaseAccess:
    access: CaseAccess
    scope: RequestScope
    case: tuple[str, str, str]  # jurisdiction, case type, specialty: for the case's packs
    context: str


def _refuse(code: int) -> WebSocketException:
    return WebSocketException(code=code)


class SocketCaseAccessDependency:
    """`require_case_access` for WebSocket routes, with the session, MFA and origin checks of `router()`."""

    def __init__(self, action: str) -> None:
        if action not in CASE_ACTIONS:
            raise ValueError(f"unknown case action {action!r}: add it to docs/spec/permissions.md first")
        self.case_action = action
        self.step_up = CASE_ACTIONS[action].step_up

    async def __call__(self, websocket: WebSocket, case_id: str) -> SocketCaseAccess:
        app_origin = websocket.app.state.app_origin
        origin = websocket.headers.get("origin")
        if origin is None or app_origin is None or origin.rstrip("/") != app_origin:
            raise _refuse(4403)
        database = websocket.app.state.database
        if database is None:
            raise _refuse(1013)
        try:
            session = await sessions.resolve(database, websocket.cookies.get(sessions.SESSION_COOKIE), touch=True)
        except ApiError as exc:
            raise _refuse(4401) from exc
        if not session.mfa_complete or (self.step_up and not session.stepped_up):
            raise _refuse(4403)
        try:
            case = uuid.UUID(case_id)
        except ValueError as exc:
            raise _refuse(4404) from exc
        scope = RequestScope(person_id=session.person_id, org_ids=(), request_id=uuid.uuid4().hex)
        try:
            async with database.request(scope) as conn:
                access = await check_case_access(conn, case, self.case_action)
                cur = await conn.execute(
                    "select jurisdiction, case_type, specialty from app.cases where id = %s", (case,)
                )
                row = await cur.fetchone()
        except ApiError as exc:
            raise _refuse(4000 + exc.status) from exc
        assert row is not None  # check_case_access has already answered 404
        if access.case_role == SUPPORT_ROLE and not session.stepped_up:
            raise _refuse(4403)
        context = websocket.query_params.get("context", "")
        if not _CODE.fullmatch(context):
            raise _refuse(4422)
        return SocketCaseAccess(access, scope, (row[0], row[1], row[2]), context)


def require_socket_case_access(action: str) -> SocketCaseAccessDependency:
    return SocketCaseAccessDependency(action)
