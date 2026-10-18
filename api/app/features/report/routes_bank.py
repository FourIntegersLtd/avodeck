"""Screen 18b: bank details on a billing profile, for the invoice's "How to pay" box.

- `PUT /me/billing-profile/bank-details`: the expert's own profile (409 `no_billing_profile` without one).
- `GET/PUT /organisations/{org_id}/billing-profile/bank-details`: an organisation's profile, for its owner or
  admin only (anyone else 404).
Every change needs step-up (rule 10) and If-Match on the profile's version, and writes an audit row
(`billing_profile.bank_details_changed`) naming the profile, never the values.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core import audit
from app.core.auth.session import current_session
from app.core.auth.step_up import check_step_up
from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag, stale
from app.features.report import bank
from app.features.report.bank import BankDetails
from app.features.report.common import not_found
from app.features.report.router import router
from app.features.report.routes_invoices import BillingProfile, _own_profile, _profile_out


class BankOut(BaseModel):
    bank: BankDetails | None


def _no_profile() -> ApiError:
    return ApiError("no_billing_profile", "Add who the invoice is from first.", 409)


async def _save(
    conn: Any, profile_id: uuid.UUID, version: int, body: BankDetails, if_match: int, request: Request
) -> None:
    if version != if_match:
        raise stale()
    check_step_up(current_session(request))
    values = {f"bank_{f}": getattr(body, f) for f in bank.FIELDS}
    sets = ", ".join(f"{col} = %({col})s" for col in values)
    cur = await conn.execute(
        f"update app.billing_profiles set {sets}, version = version + 1, updated_at = now()"  # noqa: S608
        " where id = %(id)s returning id",
        {**values, "id": profile_id},
    )
    if await cur.fetchone() is None:
        raise not_found()


@router.put("/me/billing-profile/bank-details", response_model=BillingProfile)
async def put_my_bank_details(
    body: BankDetails, if_match: IfMatch, request: Request, conn: DbConn, response: Response
) -> BillingProfile:
    row = await _own_profile(conn)
    if row is None:
        raise _no_profile()
    await _save(conn, row["id"], int(row["version"]), body, if_match, request)
    await audit.record(conn, action="billing_profile.bank_details_changed", target=f"billing_profile:{row['id']}")
    out = _profile_out(await _own_profile(conn), response).profile
    assert out is not None
    return out


async def _org_profile(conn: Any, org_id: uuid.UUID) -> dict[str, Any] | None:
    cur = await conn.execute(
        "select 1 from app.memberships where organisation_id = %s and person_id = app.current_person_id()"
        " and ended_at is null and role in ('owner', 'admin')",
        (org_id,),
    )
    if await cur.fetchone() is None:
        raise not_found()
    async with conn.cursor(row_factory=dict_row) as dcur:
        await dcur.execute(
            f"select id, version, {bank.COLUMNS} from app.billing_profiles"  # noqa: S608
            " where owner_type = 'organisation' and owner_id = %s",
            (org_id,),
        )
        row: dict[str, Any] | None = await dcur.fetchone()
    return row


@router.get("/organisations/{org_id}/billing-profile/bank-details", response_model=BankOut)
async def get_org_bank_details(org_id: uuid.UUID, conn: DbConn, response: Response) -> BankOut:
    row = await _org_profile(conn, org_id)
    if row is None:
        raise _no_profile()
    response.headers["etag"] = etag(int(row["version"]))
    return BankOut(bank=bank.from_row(row))


@router.put("/organisations/{org_id}/billing-profile/bank-details", response_model=BankOut)
async def put_org_bank_details(
    org_id: uuid.UUID, body: BankDetails, if_match: IfMatch, request: Request, conn: DbConn, response: Response
) -> BankOut:
    row = await _org_profile(conn, org_id)
    if row is None:
        raise _no_profile()
    await _save(conn, row["id"], int(row["version"]), body, if_match, request)
    await audit.record(
        conn,
        action="billing_profile.bank_details_changed",
        target=f"billing_profile:{row['id']}",
        organisation_id=org_id,
    )
    return await get_org_bank_details(org_id, conn, response)
