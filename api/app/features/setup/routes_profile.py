"""Screen 02: the expert's profile, roles (with date ranges), training and case balance (architecture 5.2)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Query, Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.http import DbConn, IfMatch, etag, stale
from app.features.setup.common import CODE, invalid, library, not_found
from app.features.setup.router import router

PACK_CODE = r"^[a-z][a-z0-9-]{0,39}$"

# --- profile -------------------------------------------------------------------------------------------


class Profile(BaseModel):
    version: int = Field(description="The same number as the ETag; send it back as If-Match.")
    full_name: str | None
    profession: str | None
    specialties: list[str]
    registration_body: str | None
    registration_number: str | None
    hourly_rate: Decimal | None
    currency: str | None
    default_retention: str | None
    default_jurisdiction: str | None


class ProfileUpdate(BaseModel):
    """Only the fields sent are changed."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    profession: str | None = Field(default=None, pattern=CODE)
    specialties: list[Annotated[str, Field(pattern=PACK_CODE)]] | None = Field(default=None, max_length=10)
    registration_body: str | None = Field(default=None, max_length=200)
    registration_number: str | None = Field(default=None, max_length=50)
    hourly_rate: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    default_retention: str | None = Field(default=None, pattern=CODE)
    default_jurisdiction: str | None = Field(default=None, pattern=PACK_CODE)


_EXPERT_FIELDS = (
    "profession",
    "specialties",
    "registration_body",
    "registration_number",
    "hourly_rate",
    "currency",
    "default_retention",
    "default_jurisdiction",
)


async def _profile(conn: DbConn) -> tuple[Profile, int]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select p.full_name, e.profession, coalesce(e.specialties, '{}') as specialties, e.registration_body,"
            " e.registration_number, e.hourly_rate, e.currency, e.default_retention, e.default_jurisdiction,"
            " coalesce((extract(epoch from e.updated_at) * 1000000)::bigint, 0) as tag,"
            " (e.person_id is null and app.is_support_staff(p.id)) as staff_only"
            " from app.people p left join app.experts e on e.person_id = p.id"
            " where p.id = app.current_person_id()"
        )
        row = await cur.fetchone()
    if row is None or row.pop("staff_only"):
        raise not_found()
    tag = int(row.pop("tag"))
    return Profile(**row, version=tag), tag


@router.get("/me/profile", response_model=Profile)
async def get_profile(conn: DbConn, response: Response) -> Profile:
    profile, tag = await _profile(conn)
    response.headers["etag"] = etag(tag)
    return profile


@router.patch("/me/profile", response_model=Profile)
async def update_profile(
    body: ProfileUpdate, if_match: IfMatch, request: Request, conn: DbConn, response: Response
) -> Profile:
    _, tag = await _profile(conn)
    if if_match != tag:
        raise stale()
    changes = body.model_dump(exclude_unset=True)
    known = set(library(request).codes("specialty"))
    if any(s not in known for s in changes.get("specialties") or []):
        raise invalid("specialties")
    if "full_name" in changes:
        await conn.execute(
            "update app.people set full_name = %s where id = app.current_person_id()", (changes.pop("full_name"),)
        )
    await conn.execute(
        "insert into app.experts (person_id) values (app.current_person_id()) on conflict (person_id) do nothing"
    )
    sets = [f"{f} = %({f})s" for f in _EXPERT_FIELDS if f in changes]
    await conn.execute(
        f"update app.experts set {', '.join([*sets, 'updated_at = clock_timestamp()'])}"  # noqa: S608
        " where person_id = app.current_person_id()",
        changes,
    )
    profile, tag = await _profile(conn)
    response.headers["etag"] = etag(tag)
    return profile


# --- roles ---------------------------------------------------------------------------------------------


class Role(BaseModel):
    id: uuid.UUID
    version: int = Field(description="The same number as the ETag; send it back as If-Match to change the role.")
    specialty: str
    role_title: str
    setting: str | None
    started_on: date
    ended_on: date | None


class RoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: str = Field(pattern=PACK_CODE)
    role_title: str = Field(min_length=1, max_length=200)
    setting: str | None = Field(default=None, pattern=CODE)
    started_on: date
    ended_on: date | None = None

    @model_validator(mode="after")
    def _ends_after_start(self) -> RoleCreate:
        if self.ended_on is not None and self.ended_on < self.started_on:
            raise ValueError("a role cannot end before it starts")
        return self


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title: str = Field(default="", min_length=1, max_length=200, validate_default=False)
    setting: str | None = Field(default=None, pattern=CODE)
    started_on: date = Field(default=date.min, validate_default=False)
    ended_on: date | None = None


_ROLE = (
    "select id, specialty, role_title, setting, lower(period) as started_on, upper(period) - 1 as ended_on,"
    " version from app.expert_roles"
)


def _check_setting(request: Request, specialty: str, setting: str | None) -> None:
    packs = library(request)
    if specialty not in packs.codes("specialty"):
        raise invalid("specialty")
    if setting is not None and setting not in packs.pack("specialty", specialty).meta.get("coverage_roles", []):
        raise invalid("setting")


async def _role(conn: DbConn, role_id: uuid.UUID) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_ROLE + " where id = %s", (role_id,))  # noqa: S608
        row = await cur.fetchone()
    if row is None:
        raise not_found()
    return row


def _role_out(row: dict[str, Any], response: Response | None = None) -> Role:
    if response is not None:
        response.headers["etag"] = etag(row["version"])
    return Role(**row)


@router.get("/me/roles", response_model=list[Role])
async def list_roles(conn: DbConn) -> list[Role]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_ROLE + " order by lower(period) desc, created_at")  # noqa: S608
        rows = await cur.fetchall()
    return [_role_out(r) for r in rows]


@router.post("/me/roles", status_code=201, response_model=Role)
async def create_role(body: RoleCreate, request: Request, conn: DbConn, response: Response) -> Role:
    _check_setting(request, body.specialty, body.setting)
    role_id = uuid.uuid4()
    await conn.execute(
        "insert into app.expert_roles (id, expert_id, specialty, role_title, setting, period)"
        " values (%s, app.current_person_id(), %s, %s, %s, daterange(%s, %s, '[]'))",
        (role_id, body.specialty, body.role_title, body.setting, body.started_on, body.ended_on),
    )
    return _role_out(await _role(conn, role_id), response)


@router.patch("/me/roles/{role_id}", response_model=Role)
async def update_role(
    role_id: uuid.UUID, body: RoleUpdate, if_match: IfMatch, request: Request, conn: DbConn, response: Response
) -> Role:
    row = await _role(conn, role_id)
    if row["version"] != if_match:
        raise stale()
    merged = {**row, **body.model_dump(exclude_unset=True)}
    if merged["ended_on"] is not None and merged["ended_on"] < merged["started_on"]:
        raise invalid("ended_on")
    _check_setting(request, merged["specialty"], merged["setting"])
    await conn.execute(
        "update app.expert_roles set role_title = %s, setting = %s, period = daterange(%s, %s, '[]') where id = %s",
        (merged["role_title"], merged["setting"], merged["started_on"], merged["ended_on"], role_id),
    )
    return _role_out(await _role(conn, role_id), response)


@router.delete("/me/roles/{role_id}", status_code=204)
async def delete_role(role_id: uuid.UUID, conn: DbConn) -> Response:
    cur = await conn.execute("delete from app.expert_roles where id = %s", (role_id,))
    if cur.rowcount == 0:
        raise not_found()
    return Response(status_code=204)


# --- training ------------------------------------------------------------------------------------------


class TrainingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    year: int | None = Field(default=None, ge=1900, le=2200)


class Training(TrainingCreate):
    id: uuid.UUID


@router.get("/me/training", response_model=list[Training])
async def list_training(conn: DbConn) -> list[Training]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select id, title, year from app.expert_training order by year desc nulls last, created_at")
        return [Training(**r) for r in await cur.fetchall()]


@router.post("/me/training", status_code=201, response_model=Training)
async def create_training(body: TrainingCreate, conn: DbConn) -> Training:
    training_id = uuid.uuid4()
    await conn.execute(
        "insert into app.expert_training (id, expert_id, title, year) values (%s, app.current_person_id(), %s, %s)",
        (training_id, body.title, body.year),
    )
    return Training(id=training_id, title=body.title, year=body.year)


@router.delete("/me/training/{training_id}", status_code=204)
async def delete_training(training_id: uuid.UUID, conn: DbConn) -> Response:
    cur = await conn.execute("delete from app.expert_training where id = %s", (training_id,))
    if cur.rowcount == 0:
        raise not_found()
    return Response(status_code=204)


# --- balance -------------------------------------------------------------------------------------------


class Balance(BaseModel):
    months: int
    counts: dict[str, int] = Field(description="Cases you are the named expert on, by party role key (from the pack)")


@router.get("/me/stats/balance", response_model=Balance)
async def get_balance(conn: DbConn, months: Annotated[int, Query(ge=1, le=120)] = 12) -> Balance:
    cur = await conn.execute(
        "select party_role, count(*) from app.cases"
        " where named_expert_id = app.current_person_id() and party_role is not null and status <> 'deleted'"
        " and created_at >= now() - make_interval(months => %s)"
        " group by party_role order by party_role",
        (months,),
    )
    return Balance(months=months, counts={str(k): int(n) for k, n in await cur.fetchall()})
