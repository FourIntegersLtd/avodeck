"""Avodeck's support console (permissions.md "Avodeck staff"; S17, F38): metadata only.

Current support staff only (anyone else 404), in a session whose MFA was completed at sign-in; no support
action needs a fresh code (ADR 0023; `require_support_lookup`, `require_support_staff`). ADR 0020. Content is
never here: under a live grant from the named expert, staff open the case through the ordinary case
routes, where `require_case_access` gives them the read-only `support` role and audits every view.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import BackgroundTasks, Depends, Header, Query, Request, Response

from app.core.auth.support import SupportStaff, require_support_lookup, require_support_staff
from app.core.flags import require_flag
from app.core.http import DbConn, keep_instead
from app.features.organisations import accounts, custody, support
from app.features.organisations.router import router
from app.features.organisations.routes_invites import _app_origin
from app.features.organisations.schemas import (
    CustodyTransfer,
    CustodyTransferCreate,
    SupportAccountCreate,
    SupportAccountList,
    SupportAccountWithPassword,
    SupportCaseDetail,
    SupportCaseList,
    SupportCellStatus,
    SupportOrganisation,
    SupportPersonList,
)

Staff = Annotated[SupportStaff, Depends(require_support_staff)]
Lookup = Annotated[SupportStaff, Depends(require_support_lookup)]
CUSTODY_FLAG = "support_custody_transfer"
# Required on account changes: a retried click must not make a second account or a second password.
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


@router.get("/support/cases", response_model=SupportCaseList)
async def search_cases(
    conn: DbConn, _: Lookup, q: Annotated[str, Query(min_length=1, max_length=320)]
) -> SupportCaseList:
    found = await support.console_search(conn, q)
    return SupportCaseList(cases=found, served_case_ids=[c.case_id for c in found if c.served])


@router.post(
    "/support/custody-transfers",
    response_model=CustodyTransfer,
    status_code=201,
    dependencies=[Depends(require_flag(CUSTODY_FLAG))],
)
async def record_custody_transfer(body: CustodyTransferCreate, conn: DbConn, staff: Staff) -> CustodyTransfer:
    """(legal check) Behind the flag `support_custody_transfer` until the legal review of custody is done."""
    return await custody.transfer(conn, staff.person_id, body)


@router.get("/support/cases/{case_id}", response_model=SupportCaseDetail)
async def look_up_case(case_id: uuid.UUID, request: Request, conn: DbConn, _: Lookup) -> SupportCaseDetail:
    """The case's grants and where it is stuck: counts, codes, statuses, dates and ids only (rule 6)."""
    return await support.console_case(conn, request.app.state.packs, case_id)


@router.get("/support/people", response_model=SupportPersonList)
async def look_up_people(
    conn: DbConn, _: Lookup, q: Annotated[str, Query(min_length=1, max_length=320)]
) -> SupportPersonList:
    """An account's state by its sign-in email: MFA, backup codes, email change, custodian, closure blockers."""
    return SupportPersonList(people=await support.console_people(conn, q))


@router.get("/support/organisations/{organisation_id}", response_model=SupportOrganisation)
async def look_up_organisation(organisation_id: uuid.UUID, conn: DbConn, _: Lookup) -> SupportOrganisation:
    """Members, invites, owners, open cases and reassignments; never a name, an email or an invite token."""
    return await support.console_organisation(conn, organisation_id)


@router.get("/support/cell/status", response_model=SupportCellStatus)
async def cell_status(request: Request, conn: DbConn, _: Lookup) -> SupportCellStatus:
    """Speech and the key service set up, the worker's heartbeat, and how much work waits or is paused."""
    state = request.app.state
    return await support.console_cell(
        conn,
        speech_ok=getattr(state, "speech", None) is not None,
        key_service_ok=getattr(state, "crypto", None) is not None,
    )


@router.get("/support/accounts", response_model=SupportAccountList)
async def list_accounts(conn: DbConn, _: Lookup) -> SupportAccountList:
    """The accounts support created: `invited` until the person has chosen their own password and completed
    MFA, then `active`."""
    return SupportAccountList(accounts=await accounts.accounts(conn))


def _shown_once(request: Request, response: Response, made: SupportAccountWithPassword) -> SupportAccountWithPassword:
    """The temporary password is in this answer only: never cached, and a repeat gets the account without it."""
    response.headers["Cache-Control"] = "no-store"
    keep_instead(request, made.model_copy(update={"temporary_password": None, "password_shown": True}))
    return made


@router.post("/support/accounts", response_model=SupportAccountWithPassword, status_code=201)
async def create_account(
    body: SupportAccountCreate, request: Request, response: Response, conn: DbConn, _: Staff, __: IdempotencyKey,
    after_commit: BackgroundTasks,
) -> SupportAccountWithPassword:  # fmt: skip
    """A new person's account with a temporary password, shown once (ADR 0024), and no team; when mail is set
    up, also a set-password email sent after commit (409 `conflict` if the email already has an account, 422
    `invalid_email` if Auth will not take it; at most 20 an hour per staff member counting refused attempts,
    429 `too_many_attempts`)."""
    state = request.app.state
    made = await accounts.create(
        conn, state.mailer, state.auth, _app_origin(request), state.region, body, after_commit,
        mail=bool(getattr(state, "mail_configured", False)),
    )  # fmt: skip
    return _shown_once(request, response, made)


@router.post("/support/accounts/{account_id}/temporary-password", response_model=SupportAccountWithPassword)
async def new_temporary_password(
    account_id: uuid.UUID, request: Request, response: Response, conn: DbConn, _: Staff, __: IdempotencyKey
) -> SupportAccountWithPassword:
    """A new temporary password, shown once, for an account still `invited`; the old one stops working and the
    person is signed out everywhere. At most once a minute (429 `sent_recently`); 409 `conflict` once active."""
    return _shown_once(request, response, await accounts.new_password(conn, request.app.state.auth, account_id))
