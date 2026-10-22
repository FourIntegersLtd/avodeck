"""`require_support_staff`: the gate for Avodeck's support console routes (permissions.md "Avodeck staff").

Staff are listed in `app.support_staff`, added and removed by hand (docs/ops/support-staff.md); no
route writes it. Someone who is not current staff gets 404, as if the console did not exist. Every
call needs MFA completed in the last 10 minutes (`aal2`), checked after the staff check. The console
shows metadata only; case content is reached through `require_case_access` under a grant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request
from psycopg import AsyncConnection

from app.core.auth.access import not_found
from app.core.auth.session import current_session
from app.core.auth.step_up import check_step_up
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
