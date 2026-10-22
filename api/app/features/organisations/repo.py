"""SQL for slice O. Writes go through slice O's database functions (migrations 1000-1099) or rows
row-level security lets the person change; foundation tables only in the columns slice O owns."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_ORG = """
select o.id, o.name, o.kind, o.region, o.policy, o.created_at, m.role
from app.memberships m join app.organisations o on o.id = m.organisation_id
where m.person_id = app.current_person_id() and m.ended_at is null and o.closed_at is null
"""


async def rows(conn: AsyncConnection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def row(conn: AsyncConnection, sql: str, params: Any = None) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()


async def value(conn: AsyncConnection, sql: str, params: Any = None) -> Any:
    cur = await conn.execute(sql, params)
    found = await cur.fetchone()
    return None if found is None else found[0]


async def my_organisations(conn: AsyncConnection) -> list[dict[str, Any]]:
    return await rows(conn, _ORG + " order by o.kind, o.name, o.id")


async def my_organisation(conn: AsyncConnection, org_id: uuid.UUID) -> dict[str, Any] | None:
    return await row(conn, _ORG + " and o.id = %s", (org_id,))


async def create_team(conn: AsyncConnection, name: str, region: str) -> uuid.UUID:
    org: uuid.UUID = await value(conn, "select app.create_team_organisation(%s, %s)", (name, region))
    return org


async def rename(conn: AsyncConnection, org_id: uuid.UUID, name: str) -> None:
    await conn.execute("update app.organisations set name = %s where id = %s", (name, org_id))


async def organisation_template_exists(conn: AsyncConnection, org_id: uuid.UUID, template_id: uuid.UUID) -> bool:
    return bool(await value(conn, "select app.organisation_template_exists(%s, %s)", (org_id, template_id)))


async def set_policy(conn: AsyncConnection, org_id: uuid.UUID, policy: dict[str, Any]) -> None:
    await conn.execute("update app.organisations set policy = %s where id = %s", (Jsonb(policy), org_id))


_PROFILE = "id, legal_name, address, tax_number, tax_rate, currency, hourly_rate, invoice_prefix, version"


async def org_billing_profile(conn: AsyncConnection, org_id: uuid.UUID) -> dict[str, Any] | None:
    return await row(
        conn,
        f"select {_PROFILE} from app.billing_profiles where owner_type = 'organisation' and owner_id = %s",  # noqa: S608
        (org_id,),
    )


async def add_org_billing_profile(conn: AsyncConnection, org_id: uuid.UUID, values: dict[str, Any]) -> uuid.UUID:
    profile_id: uuid.UUID = await value(
        conn,
        "insert into app.billing_profiles (owner_type, owner_id, legal_name, address, tax_number, tax_rate,"
        " currency, hourly_rate, invoice_prefix) values ('organisation', %(org)s, %(legal_name)s, %(address)s,"
        " %(tax_number)s, %(tax_rate)s, %(currency)s, %(hourly_rate)s, %(invoice_prefix)s) returning id",
        {**values, "org": org_id},
    )
    return profile_id


async def change_billing_profile(conn: AsyncConnection, profile_id: uuid.UUID, values: dict[str, Any]) -> None:
    await conn.execute(
        "update app.billing_profiles set legal_name = %(legal_name)s, address = %(address)s,"
        " tax_number = %(tax_number)s, tax_rate = %(tax_rate)s, currency = %(currency)s,"
        " hourly_rate = %(hourly_rate)s, invoice_prefix = %(invoice_prefix)s, version = version + 1,"
        " updated_at = now() where id = %(id)s",
        {**values, "id": profile_id},
    )


async def case_row(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, Any] | None:
    return await row(
        conn,
        "select id, organisation_id, named_expert_id, status, app.case_ever_served(id) as served"
        " from app.cases where id = %s",
        (case_id,),
    )


async def set_named_expert(conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> None:
    await conn.execute("update app.cases set named_expert_id = %s where id = %s", (person_id, case_id))


async def close_organisation(conn: AsyncConnection, org_id: uuid.UUID) -> None:
    await conn.execute("select app.close_organisation(%s)", (org_id,))


async def members(conn: AsyncConnection, org_id: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(conn, "select * from app.organisation_members(%s)", (org_id,))


async def current_membership(conn: AsyncConnection, org_id: uuid.UUID, membership_id: uuid.UUID) -> dict[str, Any] | None:
    return await row(
        conn,
        "select id, person_id, role from app.memberships where id = %s and organisation_id = %s and ended_at is null",
        (membership_id, org_id),
    )


async def my_membership_id(conn: AsyncConnection, org_id: uuid.UUID) -> uuid.UUID | None:
    found: uuid.UUID | None = await value(
        conn,
        "select id from app.memberships where organisation_id = %s and person_id = app.current_person_id()"
        " and ended_at is null",
        (org_id,),
    )
    return found


async def set_role(conn: AsyncConnection, membership_id: uuid.UUID, role: str) -> None:
    await conn.execute("update app.memberships set role = %s where id = %s", (role, membership_id))


_INVITE = """
select id, email, role, invited_by, created_at, expires_at, accepted_at,
       case when status = 'pending' and expires_at <= now() then 'expired' else status end as status
from app.invites
"""


async def invites(conn: AsyncConnection, org_id: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(conn, _INVITE + " where organisation_id = %s order by created_at, id", (org_id,))


async def invite(conn: AsyncConnection, org_id: uuid.UUID, invite_id: uuid.UUID) -> dict[str, Any] | None:
    return await row(conn, _INVITE + " where organisation_id = %s and id = %s", (org_id, invite_id))


async def add_invite(conn: AsyncConnection, org_id: uuid.UUID, email: str, role: str, token_hash: str) -> uuid.UUID:
    invite_id: uuid.UUID = await value(
        conn,
        "insert into app.invites (organisation_id, email, role, token_hash) values (%s, %s, %s, %s) returning id",
        (org_id, email, role, token_hash),
    )
    return invite_id


async def cancel_invite(conn: AsyncConnection, invite_id: uuid.UUID) -> None:
    await conn.execute("update app.invites set status = 'cancelled' where id = %s", (invite_id,))


async def accept_invite(conn: AsyncConnection, token_hash: str) -> dict[str, Any] | None:
    return await row(conn, "select * from app.accept_invite(%s)", (token_hash,))


async def end_membership(conn: AsyncConnection, membership_id: uuid.UUID) -> None:
    await conn.execute("update app.memberships set ended_at = now() where id = %s", (membership_id,))
