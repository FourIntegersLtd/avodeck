"""GET /v1/me: who is signed in. The first call creates the person and their personal organisation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import Request
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core.http import CurrentIdentity, DbConn, router
from app.core.packs import PackError, PackLibrary

me_router = router(prefix="/v1", tags=["me"])


class Person(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    locale: str | None
    timezone: str | None
    home_region: str | None
    created_at: datetime
    greeting_name: str | None = None
    password_changed_at: datetime | None = None


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
    member_count: int = 1


class Me(BaseModel):
    person: Person
    expert: Expert | None
    mfa_enrolled: bool
    memberships: list[Membership]
    is_support_staff: bool = False


def _greeting_name(library: PackLibrary, full_name: str | None, expert: dict[str, Any] | None) -> str | None:
    """The pack's title for the person's profession plus their surname ("Dr Morgan"); nothing without a title."""
    words = (full_name or "").split()
    if not words or not expert or not expert["profession"] or not expert["default_jurisdiction"]:
        return None
    try:
        titles = library.pack("jurisdiction", expert["default_jurisdiction"]).meta.get("professional_titles", {})
    except (KeyError, PackError):
        return None
    title = titles.get(expert["profession"])
    return f"{title} {words[-1]}" if title else None


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
            "select m.organisation_id, o.name, o.kind, m.role, m.started_at,"
            " (select count(*) from app.memberships x where x.organisation_id = m.organisation_id"
            "  and x.ended_at is null)::int as member_count"
            " from app.memberships m join app.organisations o on o.id = m.organisation_id"
            " where m.person_id = app.current_person_id() and m.ended_at is null"
            " order by m.started_at, m.organisation_id"
        )
        memberships = await cur.fetchall()
        await cur.execute(
            "select max(at) as at from app.audit_log"
            " where person_id = app.current_person_id() and action = 'auth.password_reset'"
        )
        reset = await cur.fetchone()
        await cur.execute("select app.is_support_staff(app.current_person_id()) as staff")
        staff = await cur.fetchone()
    assert person is not None
    return Me(
        person=Person(
            **person,
            greeting_name=_greeting_name(request.app.state.packs, person["full_name"], expert),
            password_changed_at=reset["at"] if reset else None,
        ),
        expert=Expert(**expert) if expert else None,
        mfa_enrolled=identity.mfa_enrolled,
        memberships=[Membership(**m) for m in memberships],
        is_support_staff=bool(staff and staff["staff"]),
    )
