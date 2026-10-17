"""GET and PATCH /v1/cases/{case_id}: the case's details, what you may do on it, and its ETag.

Status is never set here: it changes only through the state machine (`app.core.state_machines`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, Request, Response
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.auth.access import CaseAccess, check_case_access, require_case_access
from app.core.errors import ApiError
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
    """Only the fields sent are changed. Party and track values must be keys the case's packs offer."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", min_length=1, max_length=200, validate_default=False)
    due_date: date | None = None
    party_role: str | None = Field(default=None, max_length=100)
    instructing_party_type: str | None = Field(default=None, max_length=100)
    court_track: str | None = Field(default=None, max_length=100)


class CaseCreate(BaseModel):
    """A draft case. Every pack value is a key of a combination this cell offers (rule 11)."""

    model_config = ConfigDict(extra="forbid")

    organisation_id: uuid.UUID
    jurisdiction: str = Field(max_length=100)
    case_type: str = Field(max_length=100)
    specialty: str = Field(max_length=100)
    expert_role: str | None = Field(default=None, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    party_role: str | None = Field(default=None, max_length=100)
    instructing_party_type: str | None = Field(default=None, max_length=100)
    court_track: str | None = Field(default=None, max_length=100)
    due_date: date | None = None


_CREATORS = ("owner", "admin", "expert")


def _invalid(field: str) -> ApiError:
    return ApiError("invalid", "That value is not offered here.", 422, field=field)


def _checked(request: Request, body: CaseCreate) -> dict[str, object]:
    """The row to insert, with values checked against the cell's packs and the pack versions stamped."""
    library = request.app.state.packs
    offered = {(j.code, ct.code, sp.code) for j, ct, sp in library.combinations_in(request.app.state.region)}
    if (body.jurisdiction, body.case_type, body.specialty) not in offered:
        raise _invalid("jurisdiction")
    pack = library.combine(body.jurisdiction, body.case_type, body.specialty)
    roles = pack.expert_roles()
    expert_role = body.expert_role or roles[0]
    if expert_role not in roles:
        raise _invalid("expert_role")
    _check_pack_values(request, (body.jurisdiction, body.case_type, body.specialty), body.model_dump())
    return {**body.model_dump(), "expert_role": expert_role, "pack_versions": Jsonb(pack.versions)}


def _check_pack_values(request: Request, packs: tuple[str, str, str], values: dict[str, object]) -> None:
    """Party role, instructing-party type and court track must be keys the packs offer (422 `invalid`)."""
    library = request.app.state.packs
    pack = library.combine(*packs)
    offered = {
        "party_role": [p["key"] for p in pack.party_roles()],
        "instructing_party_type": [p["key"] for p in pack.instructing_party_types()],
        "court_track": library.jurisdiction_summary(packs[0])["court_tracks"],
    }
    for field, allowed in offered.items():
        value = values.get(field)
        if value is not None and value not in allowed:
            raise _invalid(field)


@cases_router.post("", status_code=201, response_model=Case)
async def create_case(body: CaseCreate, request: Request, conn: DbConn, response: Response) -> Case:
    row = _checked(request, body)
    cur = await conn.execute(
        "select 1 from app.memberships where organisation_id = %s and person_id = app.current_person_id()"
        " and ended_at is null and role = any(%s)",
        (body.organisation_id, list(_CREATORS)),
    )
    if await cur.fetchone() is None:
        raise ApiError("forbidden", "You cannot create a case for this organisation.", 403)
    case_id = uuid.uuid4()  # no RETURNING: the select policy cannot see a row in the statement that inserts it
    columns = ["id", "named_expert_id", *row]
    await conn.execute(
        sql.SQL("insert into app.cases ({}) values (%s, app.current_person_id(), {})").format(
            sql.SQL(", ").join(map(sql.Identifier, columns)), sql.SQL(", ").join(sql.Placeholder() * len(row))
        ),
        (case_id, *row.values()),
    )
    await audit.record(
        conn, action="case.created", target=f"case:{case_id}", case_id=case_id, organisation_id=body.organisation_id
    )
    access = await check_case_access(conn, case_id, "case.view_metadata")
    async with conn.cursor(row_factory=dict_row) as cur2:
        await cur2.execute(sql.SQL("select {} from app.cases where id = %s").format(sql.SQL(_COLUMNS)), (case_id,))
        created = await cur2.fetchone()
    assert created is not None
    return _case(created, access, response)


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
    request: Request,
    conn: DbConn,
    response: Response,
) -> Case:
    changes = body.model_dump(exclude_unset=True)
    if any(changes.get(f) is not None for f in ("party_role", "instructing_party_type", "court_track")):
        cur0 = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
        packs = await cur0.fetchone()
        assert packs is not None  # require_case_access has already answered 404
        _check_pack_values(request, (packs[0], packs[1], packs[2]), changes)
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
