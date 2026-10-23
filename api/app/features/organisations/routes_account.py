"""The person's own account (architecture 5.4): name a records custodian 🔒."""

from __future__ import annotations

from app.core.http import CurrentScope, DbConn
from app.features.organisations import account
from app.features.organisations.deps import STEP_UP_FIRST
from app.features.organisations.router import router
from app.features.organisations.schemas import AccountClosed, CloseAccountPreview, Custodian


@router.get("/me/close", response_model=CloseAccountPreview)
async def close_account_preview(conn: DbConn, scope: CurrentScope) -> CloseAccountPreview:
    return await account.close_preview(conn, scope.person_id)


@router.post("/me/close", response_model=AccountClosed, dependencies=STEP_UP_FIRST)
async def close_account(conn: DbConn, scope: CurrentScope) -> AccountClosed:
    return await account.close_account(conn, scope.person_id)


@router.put("/me/custodian", response_model=Custodian, dependencies=STEP_UP_FIRST)
async def set_custodian(body: Custodian, conn: DbConn, scope: CurrentScope) -> Custodian:
    return await account.set_custodian(conn, scope.person_id, body)
