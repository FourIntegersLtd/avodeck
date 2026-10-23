"""Custody of a served case's records when its expert can no longer act (architecture 10.1 "Death or
incapacity"; ADR 0012, legal check). SQL lives in `repo.py`.

Support staff record it, with a short note of the evidence seen (about the expert, never the patient), in
`custody_transfers` (migration 0032, foundation's table and access rule: the recipient gets the read-only
`custodian` case role). The recipient is either the records custodian the named expert recorded (an open
account that is neither support staff nor the recorder) or the case's own team organisation.
One holder at a time: revoke a transfer before recording another. A team organisation that has closed holds
nothing (its memberships have ended), so it cannot receive one. A refused attempt is audited as
`support.custody_transfer_refused` (target `refused:<code>`), in a transaction of its own so it stays although
the request rolls back (`audit.record_detached`).
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import not_found
from app.core.errors import ApiError
from app.features.organisations import repo
from app.features.organisations.schemas import CustodyTransfer, CustodyTransferCreate

TRANSFERRED = "support.custody_transferred"
REFUSED = "support.custody_transfer_refused"


def _invalid(code: str, message: str) -> ApiError:
    return ApiError(code, message, 422, fields=["to"])


async def _custodian(conn: AsyncConnection, case_id: uuid.UUID, recorder: uuid.UUID) -> uuid.UUID:
    found = await repo.support_case_custodian(conn, case_id)
    if found is None:
        raise _invalid("no_custodian", "The expert has not named a records custodian with an account.")
    if found["closed"]:
        raise _invalid("custodian_account_closed", "The named records custodian's account is closed.")
    if found["staff"] or found["person_id"] == recorder:
        raise _invalid("custodian_not_allowed", "Support staff cannot hold a case's records.")
    person: uuid.UUID = found["person_id"]
    return person


async def transfer(conn: AsyncConnection, recorder: uuid.UUID, body: CustodyTransferCreate) -> CustodyTransfer:
    try:
        return await _transfer(conn, recorder, body)
    except ApiError as refused:
        audit.record_detached(action=REFUSED, target=f"refused:{refused.code}", case_id=body.case_id)
        raise


async def _transfer(conn: AsyncConnection, recorder: uuid.UUID, body: CustodyTransferCreate) -> CustodyTransfer:
    cases = await repo.support_cases(conn, body.case_id, None)
    if not cases:
        raise not_found()
    case = cases[0]
    if not case["served"]:
        raise ApiError("not_served", "Only a served case's records pass to a custodian.", 409)
    if await repo.custody_in_force(conn, body.case_id):
        raise ApiError("custody_already_transferred", "This case's records are already held by someone.", 409)
    to_person: uuid.UUID | None = None
    to_org: uuid.UUID | None = None
    if body.to == "custodian":
        to_person = await _custodian(conn, body.case_id, recorder)
    elif case["organisation_kind"] != "team":
        raise _invalid("personal_organisation", "A personal practice has no one else to hold its records.")
    elif await repo.support_organisation_closed(conn, case["organisation_id"]):
        raise ApiError("organisation_closed", "This organisation has closed, so it cannot hold the records.", 409)
    else:
        to_org = case["organisation_id"]
    made = await repo.add_custody_transfer(conn, body.case_id, to_person, to_org, body.evidence_note)
    await audit.record(conn, action=TRANSFERRED, target=f"custody:{made['id']}", case_id=body.case_id)
    return CustodyTransfer(**made)
