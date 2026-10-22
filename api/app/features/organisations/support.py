"""Support access to one case (architecture 10.1 "Support access"; S17, F38). SQL lives in `repo.py`.

The named expert gives Avodeck support at most 24 hours on one case (`support_access_grants`, migration
0030, which also checks the grant on every read in `app.case_access()`). One active grant per case: a new
one ends the earlier. Grants and revocations go to the case's audit trail, with ids and a code only.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import not_found
from app.core.email import Mailer, support_access_granted_mail
from app.core.errors import ApiError, conflict
from app.core.notifications import send_mail
from app.features.organisations import repo
from app.features.organisations.schemas import SupportGrant, SupportGrantCreate


async def grants(conn: AsyncConnection, case_id: uuid.UUID) -> list[SupportGrant]:
    return [SupportGrant(**r) for r in await repo.grants(conn, case_id)]


async def give(
    conn: AsyncConnection, mailer: Mailer, case_id: uuid.UUID, body: SupportGrantCreate, expert_email: str
) -> SupportGrant:
    if body.granted_to is not None and not await repo.is_support_staff(conn, body.granted_to):
        raise ApiError("invalid", "Some details are not valid.", 422, fields=["granted_to"])
    await repo.end_active_grants(conn, case_id)
    grant_id = await repo.add_grant(conn, case_id, body.reason, body.granted_to)
    given = await repo.grant(conn, case_id, grant_id)
    assert given is not None
    await audit.record(conn, action="support_access.granted", target=f"grant:{grant_id}", case_id=case_id)
    mail = support_access_granted_mail(expert_email, case_id=case_id, grant_id=grant_id, expires_at=given["expires_at"])
    await send_mail(mailer, mail)
    return SupportGrant(**given)


async def revoke(conn: AsyncConnection, case_id: uuid.UUID, grant_id: uuid.UUID) -> None:
    found = await repo.grant(conn, case_id, grant_id)
    if found is None:
        raise not_found()
    if not found["active"]:
        raise conflict()
    await repo.revoke_grant(conn, grant_id)
    await audit.record(conn, action="support_access.revoked", target=f"grant:{grant_id}", case_id=case_id)
