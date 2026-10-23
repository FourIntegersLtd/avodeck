"""Custody of a served case's records when its expert can no longer act (architecture 10.1 "Death or
incapacity"; ADR 0012, legal check). SQL lives in `repo.py`.

Support staff record it, with a short note of the evidence seen (about the expert, never the patient), in
`custody_transfers` (migration 0032, foundation's table and access rule: the recipient gets the read-only
`custodian` case role). The recipient is either the records custodian the named expert recorded (an open
account that is neither support staff nor the recorder) or the case's own team organisation.
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
    cases = await repo.support_cases(conn, body.case_id, None)
    if not cases:
        raise not_found()
    case = cases[0]
    if not case["served"]:
        raise ApiError("not_served", "Only a served case's records pass to a custodian.", 409)
    to_person: uuid.UUID | None = None
    to_org: uuid.UUID | None = None
    if body.to == "custodian":
        to_person = await _custodian(conn, body.case_id, recorder)
    elif case["organisation_kind"] != "team":
        raise _invalid("personal_organisation", "A personal practice has no one else to hold its records.")
    else:
        to_org = case["organisation_id"]
    made = await repo.add_custody_transfer(conn, body.case_id, to_person, to_org, body.evidence_note)
    await audit.record(conn, action=TRANSFERRED, target=f"custody:{made['id']}", case_id=body.case_id)
    return CustodyTransfer(**made)
