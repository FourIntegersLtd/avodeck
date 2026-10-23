"""Avodeck's support console (permissions.md "Avodeck staff"; S17, F38): metadata only.

Current support staff only (`require_support_staff`; anyone else 404), with MFA in the last 10 minutes on
every call, checked first (one answer for everyone, so it says nothing about who is staff). Content is
never here: under a live grant from the named expert, staff open the case through the ordinary case
routes, where `require_case_access` gives them the read-only `support` role and audits every view.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Query

from app.core.auth.support import SupportStaff, require_support_staff
from app.core.http import DbConn
from app.features.organisations import support
from app.features.organisations.deps import STEP_UP_FIRST
from app.features.organisations.router import router
from app.features.organisations.schemas import SupportCaseDetail, SupportCaseList

Staff = Annotated[SupportStaff, Depends(require_support_staff)]


@router.get("/support/cases", response_model=SupportCaseList, dependencies=STEP_UP_FIRST)
async def search_cases(
    conn: DbConn, _: Staff, q: Annotated[str, Query(min_length=1, max_length=320)]
) -> SupportCaseList:
    return SupportCaseList(cases=await support.console_search(conn, q))


@router.get("/support/cases/{case_id}", response_model=SupportCaseDetail, dependencies=STEP_UP_FIRST)
async def look_up_case(case_id: uuid.UUID, conn: DbConn, _: Staff) -> SupportCaseDetail:
    return await support.console_case(conn, case_id)
