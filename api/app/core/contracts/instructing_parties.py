"""Instructing parties and their instructions (slice C fills them; slice H reads them).

- `instructing_party_contacts(conn, case_id)`: the parties instructing the case and their email, for the
  certificate of deletion. Read them before `app.purge_case()` removes the rows.
- `instructing_party_requests(conn, case_id, crypto)`: every set of instructions the expert received
  (letters, supplementary letters, oral instructions with their words), oldest first, for the export.

Both run under row-level security as the caller (a person in a request, or the worker in the case's own
job), so a person outside the case gets nothing. Names, emails and oral words are personal data: never
log them (rule 6).
"""

from __future__ import annotations

import uuid
from datetime import date

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from app.core.crypto import CaseCrypto

__all__ = [
    "ORAL_INSTRUCTION_PURPOSE",
    "InstructingPartyContact",
    "InstructingPartyRequest",
    "instructing_party_contacts",
    "instructing_party_requests",
]

# The purpose oral instruction words are sealed under (`CaseCrypto.encrypt(..., purpose=...)`).
ORAL_INSTRUCTION_PURPOSE = "oral_instruction"


class InstructingPartyContact(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    type_key: str
    name: str
    firm: str
    email: str | None
    reference: str | None
    effective_from: date
    effective_to: date | None
    current: bool


class InstructingPartyRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    instruction_version_id: uuid.UUID
    version_no: int
    kind: str
    document_id: uuid.UUID | None
    received_on: date | None
    locked: bool
    text: str | None
    source: str | None


async def instructing_party_contacts(
    conn: AsyncConnection, case_id: uuid.UUID, *, current_only: bool = True
) -> list[InstructingPartyContact]:
    """Current parties (no end date) by default; every party the case has had with `current_only=False`."""
    sql = (
        "select id, type_key, name, firm, email, reference, effective_from, effective_to,"
        " effective_to is null as current from app.case_instructing_parties where case_id = %s"
    )
    if current_only:
        sql += " and effective_to is null"
    sql += " order by effective_from, created_at, id"
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, (case_id,))
        return [InstructingPartyContact(**r) for r in await cur.fetchall()]


async def instructing_party_requests(
    conn: AsyncConnection, case_id: uuid.UUID, crypto: CaseCrypto
) -> list[InstructingPartyRequest]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select v.id as instruction_version_id, v.version_no, v.kind, v.document_id,"
            " coalesce(i.received_on, v.received_on) as received_on, v.locked_at is not null as locked,"
            " i.text, i.source from app.instruction_versions v"
            " left join app.case_instructions i on i.instruction_version_id = v.id"
            " where v.case_id = %s order by v.version_no",
            (case_id,),
        )
        rows = await cur.fetchall()
    out = []
    for r in rows:
        if r["text"] is not None:
            sealed = bytes(r["text"])
            r["text"] = (await crypto.decrypt(conn, case_id, sealed, purpose=ORAL_INSTRUCTION_PURPOSE)).decode()
        out.append(InstructingPartyRequest(**r))
    return out
