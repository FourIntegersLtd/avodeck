"""GET /v1/me: who is signed in. The first call creates the person and their personal organisation."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import Request
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core.http import CurrentIdentity, DbConn, router

me_router = router(prefix="/v1", tags=["me"])


class Person(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    locale: str | None
    timezone: str | None
    home_region: str | None
    created_at: datetime


class Expert(BaseModel):
    profession: str | None
    specialties: list[str]
    registration_body: str | None
    default_jurisdiction: str | None


class Membership(BaseModel):
    organisation_id: uuid.UUID
    name: str
    kind: str
    role: str
    started_at: datetime


class Me(BaseModel):
    person: Person
    expert: Expert | None
    mfa_enrolled: bool
    memberships: list[Membership]


@me_router.get("/me", response_model=Me)
async def get_me(request: Request, identity: CurrentIdentity, conn: DbConn) -> Me:
    region: str = request.app.state.region
    await conn.execute("select app.ensure_person(%s, %s, %s)", (identity.email, identity.full_name, region))
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, email, full_name, locale, timezone, home_region, created_at from app.people"
            " where id = app.current_person_id()"
        )
        person = await cur.fetchone()
        await cur.execute(
            "select profession, specialties, registration_body, default_jurisdiction from app.experts"
            " where person_id = app.current_person_id()"
        )
        expert = await cur.fetchone()
        await cur.execute(
            "select m.organisation_id, o.name, o.kind, m.role, m.started_at"
            " from app.memberships m join app.organisations o on o.id = m.organisation_id"
            " where m.person_id = app.current_person_id() and m.ended_at is null"
            " order by m.started_at, m.organisation_id"
        )
        memberships = await cur.fetchall()
    assert person is not None
    return Me(
        person=Person(**person),
        expert=Expert(**expert) if expert else None,
        mfa_enrolled=identity.mfa_enrolled,
        memberships=[Membership(**m) for m in memberships],
    )
