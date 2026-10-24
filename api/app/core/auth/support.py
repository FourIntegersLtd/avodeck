"""The gates for Avodeck's support console routes (permissions.md "Avodeck staff"; ADR 0020).

Staff are listed in `app.support_staff`, added and removed by hand (docs/ops/support-staff.md); no
route writes it. Someone who is not current staff gets 404, as if the console did not exist.

- `require_support_lookup`, for read-only lookups (`GET /v1/support/...`): current staff, in a session
  whose MFA was completed at sign-in (`aal2`, `mfa_verified_at` set; else 403 `mfa_required`). The
  session's 30-minute idle and 12-hour limits still apply; the 10-minute window does not.
- `require_support_staff`, for every support route that changes something: current staff, and MFA
  completed in the last 10 minutes (403 `step_up_required`), checked after the staff check.

The console shows metadata only and every lookup is audited; case content is reached through
`require_case_access` under a grant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request
from psycopg import AsyncConnection

from app.core.auth.access import not_found
from app.core.auth.session import Session, current_session
from app.core.auth.step_up import check_step_up
from app.core.errors import ApiError
from app.core.http import DbConn


@dataclass(frozen=True)
class SupportStaff:
    person_id: uuid.UUID


async def check_support_staff(conn: AsyncConnection) -> SupportStaff:
    cur = await conn.execute(
        "select app.current_person_id() from app.support_staff"
        " where person_id = app.current_person_id() and removed_at is null"
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    return SupportStaff(row[0])


class _SupportStaff:
    """A FastAPI dependency; `step_up` lets the route scanners see that every call needs fresh MFA."""

    step_up = True

    async def __call__(self, request: Request, conn: DbConn) -> SupportStaff:
        staff = await check_support_staff(conn)
        check_step_up(current_session(request))
        return staff


require_support_staff = _SupportStaff()


def check_session_mfa(session: Session) -> None:
    if not session.mfa_complete or session.mfa_verified_at is None:
        raise ApiError("mfa_required", "Enter a code from your authenticator app to continue.", 403)


class _SupportLookup:
    """A FastAPI dependency for read-only support lookups; `step_up` is False: MFA at sign-in is enough."""

    step_up = False

    async def __call__(self, request: Request, conn: DbConn) -> SupportStaff:
        check_session_mfa(current_session(request))
        return await check_support_staff(conn)


require_support_lookup = _SupportLookup()
