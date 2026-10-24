"""Avodeck's support console (permissions.md "Avodeck staff"; S17, F38): metadata only.

Current support staff only (`require_support_staff`; anyone else 404), with MFA in the last 10 minutes on
every call, checked first (one answer for everyone, so it says nothing about who is staff). Content is
never here: under a live grant from the named expert, staff open the case through the ordinary case
routes, where `require_case_access` gives them the read-only `support` role and audits every view.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Query, Request

from app.core.auth.support import SupportStaff, require_support_staff
from app.core.flags import require_flag
from app.core.http import DbConn
from app.features.organisations import custody, support
from app.features.organisations.deps import STEP_UP_FIRST
from app.features.organisations.router import router
from app.features.organisations.schemas import (
    CustodyTransfer,
    CustodyTransferCreate,
    SupportCaseDetail,
    SupportCaseList,
)

Staff = Annotated[SupportStaff, Depends(require_support_staff)]
CUSTODY_FLAG = "support_custody_transfer"


@router.get("/support/cases", response_model=SupportCaseList, dependencies=STEP_UP_FIRST)
async def search_cases(
    conn: DbConn, _: Staff, q: Annotated[str, Query(min_length=1, max_length=320)]
) -> SupportCaseList:
    return SupportCaseList(cases=await support.console_search(conn, q))


@router.post(
    "/support/custody-transfers",
    response_model=CustodyTransfer,
    status_code=201,
    dependencies=[*STEP_UP_FIRST, Depends(require_flag(CUSTODY_FLAG))],
)
async def record_custody_transfer(body: CustodyTransferCreate, conn: DbConn, staff: Staff) -> CustodyTransfer:
    """(legal check) Behind the flag `support_custody_transfer` until the legal review of custody is done."""
    return await custody.transfer(conn, staff.person_id, body)


@router.get("/support/cases/{case_id}", response_model=SupportCaseDetail, dependencies=STEP_UP_FIRST)
async def look_up_case(case_id: uuid.UUID, request: Request, conn: DbConn, _: Staff) -> SupportCaseDetail:
    """The case's grants and where it is stuck: counts, codes, statuses, dates and ids only (rule 6)."""
    return await support.console_case(conn, request.app.state.packs, case_id)
