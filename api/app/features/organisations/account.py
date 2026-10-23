"""The person's own account (architecture 10.1): records custodian and account closure. SQL lives in `repo.py`."""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import not_found
from app.core.contracts.close import case_export_done, request_case_deletion
from app.core.errors import ApiError
from app.features.organisations import repo
from app.features.organisations.schemas import AccountClosed, CloseAccountCase, CloseAccountPreview, Custodian
from app.features.organisations.service import refusals


def export_first(case_ids: list[uuid.UUID]) -> ApiError:
    return ApiError(
        "export_first",
        "Export these cases before you close your account.",
        409,
        case_ids=[str(c) for c in case_ids],
    )


async def close_account(conn: AsyncConnection, person_id: uuid.UUID) -> AccountClosed:
    """Scenario S15 (legal check): unserved personal cases are deleted after export (slice H's job), team
    memberships end and the account becomes sign-in-only for the served cases it is named on."""
    if await repo.account_closed(conn):
        raise not_found()
    unserved = await repo.unserved_personal_cases(conn)
    waiting = [c for c in unserved if not await case_export_done(conn, c, person_id)]
    if waiting:
        raise export_first(waiting)
    async with refusals():
        for membership_id in await repo.my_team_memberships(conn):
            await repo.end_membership(conn, membership_id)
    jobs = [await request_case_deletion(conn, case_id) for case_id in unserved]
    closed_at = await repo.close_account(conn)
    await audit.record(conn, action="person.closed", target=f"person:{person_id}")
    return AccountClosed(closed_at=closed_at, deletion_job_ids=jobs)


async def set_custodian(conn: AsyncConnection, person_id: uuid.UUID, custodian: Custodian) -> Custodian:
    """Names or clears the custodian; the audit row holds the person's id only, never the custodian's details."""
    await repo.set_custodian(conn, custodian.name, custodian.email)
    await audit.record(conn, action="person.custodian_changed", target=f"person:{person_id}")
    return custodian


async def close_preview(conn: AsyncConnection, person_id: uuid.UUID) -> CloseAccountPreview:
    found = await repo.unserved_personal_case_labels(conn)
    cases = [
        CloseAccountCase(case_id=c["id"], label=c["label"], exported=await case_export_done(conn, c["id"], person_id))
        for c in found
    ]
    return CloseAccountPreview(cases=cases)
