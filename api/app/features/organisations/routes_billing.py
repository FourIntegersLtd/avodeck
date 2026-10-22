"""A team's billing profile (architecture 5.4): owner or admin; a new tax number needs step-up.

Bank details have their own routes (slice F, `/organisations/{org_id}/billing-profile/bank-details`).
"""

from __future__ import annotations

import uuid

from fastapi import Response

from app.core.http import CurrentSession, DbConn, IfMatch, etag
from app.features.organisations import service
from app.features.organisations.deps import org_role
from app.features.organisations.router import router
from app.features.organisations.schemas import BillingProfileIn, BillingProfileOut

Billing = org_role("org.billing")


@router.get("/organisations/{org_id}/billing-profile", response_model=BillingProfileOut)
async def get_billing_profile(org_id: uuid.UUID, conn: DbConn, access: Billing, response: Response) -> BillingProfileOut:
    profile, version = await service.billing_profile(conn, access)
    response.headers["etag"] = etag(version)
    return BillingProfileOut(profile=profile)


@router.put("/organisations/{org_id}/billing-profile", response_model=BillingProfileOut)
async def put_billing_profile(
    org_id: uuid.UUID,
    body: BillingProfileIn,
    if_match: IfMatch,
    conn: DbConn,
    session: CurrentSession,
    access: Billing,
    response: Response,
) -> BillingProfileOut:
    profile, version = await service.save_billing_profile(conn, access, body, if_match, session)
    response.headers["etag"] = etag(version)
    return BillingProfileOut(profile=profile)
