"""GET and PATCH /v1/cases/{case_id}: the case's details, what you may do on it, and its ETag.

Status is never set here: it changes only through the state machine (`app.core.state_machines`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, Response
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.http import DbConn, IfMatch, etag, router, stale

cases_router = router(prefix="/v1/cases", tags=["cases"])

_COLUMNS = (
    "id, organisation_id, named_expert_id, jurisdiction, case_type, specialty, expert_role, label,"
    " party_role, instructing_party_type, court_track, due_date, status, on_hold, version, created_at, updated_at"
)


class Case(BaseModel):
    id: uuid.UUID
    organisation_id: uuid.UUID
    named_expert_id: uuid.UUID
    jurisdiction: str
    case_type: str
    specialty: str
    expert_role: str
    label: str
    party_role: str | None
    instructing_party_type: str | None
    court_track: str | None
    due_date: date | None
    status: str
    on_hold: bool
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class CaseUpdate(BaseModel):
    """Only the fields sent are changed. Party and track values are pack keys, checked by slice C."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", min_length=1, max_length=200, validate_default=False)
    due_date: date | None = None
    party_role: str | None = Field(default=None, max_length=100)
    instructing_party_type: str | None = Field(default=None, max_length=100)
    court_track: str | None = Field(default=None, max_length=100)


def _case(row: dict[str, object], access: CaseAccess, response: Response) -> Case:
    response.headers["etag"] = etag(int(row.pop("version")))  # type: ignore[call-overload]
    return Case(**row, allowed_actions=list(access.allowed_actions))  # type: ignore[arg-type]


@cases_router.get("/{case_id}", response_model=Case)
async def get_case(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.view_metadata"))],
    conn: DbConn,
    response: Response,
) -> Case:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql.SQL("select {} from app.cases where id = %s").format(sql.SQL(_COLUMNS)), (case_id,))
        row = await cur.fetchone()
    assert row is not None  # require_case_access has already answered 404
    return _case(row, access, response)


@cases_router.patch("/{case_id}", response_model=Case)
async def update_case(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.edit_setup"))],
    version: IfMatch,
    body: CaseUpdate,
    conn: DbConn,
    response: Response,
) -> Case:
    changes = body.model_dump(exclude_unset=True)
    assignments = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in changes)
    query = (
        sql.SQL("update app.cases set {} where id = %s and version = %s returning {}").format(
            assignments, sql.SQL(_COLUMNS)
        )
        if changes
        else sql.SQL("update app.cases set version = version where id = %s and version = %s returning {}").format(
            sql.SQL(_COLUMNS)
        )
    )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, (*changes.values(), case_id, version))
        row = await cur.fetchone()
    if row is None:
        raise stale()
    await audit.record(
        conn, action="case.updated", target=f"case:{case_id}", case_id=case_id, organisation_id=row["organisation_id"]
    )
    return _case(row, access, response)
