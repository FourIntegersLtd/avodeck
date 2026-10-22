"""SQL for slice O. Writes go through slice O's database functions (migrations 1000-1099) or rows
row-level security lets the person change; foundation tables only in the columns slice O owns."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

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
