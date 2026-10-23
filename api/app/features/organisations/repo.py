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
  and exists (select 1 from app.people p where p.id = m.person_id and p.closed_at is null)
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


async def current_membership(
    conn: AsyncConnection, org_id: uuid.UUID, membership_id: uuid.UUID
) -> dict[str, Any] | None:
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


# A grant counts only while it is live and was given by the case's current named expert (as in
# `app.case_access()` and `app.support_case_grants()`): after reassignment the old expert's grant is ended.
_GRANT = """
select g.id, g.case_id, g.granted_by, g.granted_to, g.reason, g.granted_at, g.expires_at, g.revoked_at,
       g.revoked_at is null and g.expires_at > now() and g.granted_by = c.named_expert_id as active
from app.support_access_grants g join app.cases c on c.id = g.case_id
"""


async def lock_case(conn: AsyncConnection, case_id: uuid.UUID) -> None:
    """Serialises grant changes on one case, so two grants given at once cannot both stay active."""
    await conn.execute("select id from app.cases where id = %s for update", (case_id,))


async def is_support_staff(conn: AsyncConnection, person_id: uuid.UUID) -> bool:
    return bool(await value(conn, "select app.is_support_staff(%s)", (person_id,)))


async def end_active_grants(conn: AsyncConnection, case_id: uuid.UUID) -> None:
    await conn.execute(
        "update app.support_access_grants set revoked_at = now()"
        " where case_id = %s and revoked_at is null and expires_at > now()",
        (case_id,),
    )


async def add_grant(conn: AsyncConnection, case_id: uuid.UUID, reason: str, granted_to: uuid.UUID | None) -> uuid.UUID:
    grant_id: uuid.UUID = await value(
        conn,
        "insert into app.support_access_grants (case_id, granted_by, granted_to, reason)"
        " values (%s, app.current_person_id(), %s, %s) returning id",
        (case_id, granted_to, reason),
    )
    return grant_id


async def grants(conn: AsyncConnection, case_id: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(conn, _GRANT + " where g.case_id = %s order by g.granted_at, g.id", (case_id,))


async def grant(conn: AsyncConnection, case_id: uuid.UUID, grant_id: uuid.UUID) -> dict[str, Any] | None:
    return await row(conn, _GRANT + " where g.case_id = %s and g.id = %s", (case_id, grant_id))


async def revoke_grant(conn: AsyncConnection, grant_id: uuid.UUID) -> None:
    await conn.execute("update app.support_access_grants set revoked_at = now() where id = %s", (grant_id,))


async def support_cases(conn: AsyncConnection, case_id: uuid.UUID | None, email: str | None) -> list[dict[str, Any]]:
    return await rows(conn, "select * from app.support_cases(%s, %s)", (case_id, email))


async def support_case_grants(conn: AsyncConnection, case_id: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(conn, "select * from app.support_case_grants(%s)", (case_id,))


async def support_case_custodian(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, Any] | None:
    return await row(conn, "select * from app.support_case_custodian(%s) limit 1", (case_id,))


async def custody_in_force(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    """Waits for any other transfer of this case in flight (transaction lock), then: is one in force?"""
    await conn.execute("select pg_advisory_xact_lock(hashtextextended('custody:' || %s::text, 0))", (case_id,))
    return bool(
        await value(
            conn, "select exists (select 1 from app.custody_transfers where case_id = %s and revoked_at is null)",
            (case_id,),
        )  # fmt: skip
    )


async def add_custody_transfer(
    conn: AsyncConnection, case_id: uuid.UUID, to_person: uuid.UUID | None, to_org: uuid.UUID | None, note: str
) -> dict[str, Any]:
    made = await row(
        conn,
        "insert into app.custody_transfers (case_id, to_person_id, to_organisation_id, evidence_note, transferred_by)"
        " values (%s, %s, %s, %s, app.current_person_id())"
        " returning id, case_id, to_person_id, to_organisation_id, transferred_at",
        (case_id, to_person, to_org, note),
    )
    assert made is not None
    return made


async def account_closed(conn: AsyncConnection) -> bool:
    return bool(await value(conn, "select closed_at is not null from app.people where id = app.current_person_id()"))


async def unserved_personal_cases(conn: AsyncConnection) -> list[uuid.UUID]:
    """My own practice's cases on which I am the named expert, never served, not deleted or being deleted."""
    found = await rows(
        conn,
        """
        select c.id from app.cases c join app.organisations o on o.id = c.organisation_id
        where o.kind = 'personal' and c.named_expert_id = app.current_person_id() and c.status <> 'deleted'
          and c.retention_policy is distinct from 'delete_now' and not app.case_ever_served(c.id)
        order by c.created_at, c.id
        """,
    )
    return [r["id"] for r in found]


async def unserved_personal_case_labels(conn: AsyncConnection) -> list[dict[str, Any]]:
    """The same cases as `unserved_personal_cases`, with their labels (a case's label is not patient text)."""
    return await rows(
        conn,
        """
        select c.id, c.label from app.cases c join app.organisations o on o.id = c.organisation_id
        where o.kind = 'personal' and c.named_expert_id = app.current_person_id() and c.status <> 'deleted'
          and c.retention_policy is distinct from 'delete_now' and not app.case_ever_served(c.id)
        order by c.created_at, c.id
        """,
    )


async def my_team_memberships(conn: AsyncConnection) -> list[uuid.UUID]:
    found = await rows(
        conn,
        "select m.id from app.memberships m join app.organisations o on o.id = m.organisation_id"
        " where m.person_id = app.current_person_id() and m.ended_at is null and o.kind = 'team'",
    )
    return [r["id"] for r in found]


async def close_account(conn: AsyncConnection) -> Any:
    return await value(
        conn, "update app.people set closed_at = now() where id = app.current_person_id() returning closed_at"
    )


async def set_custodian(conn: AsyncConnection, name: str | None, email: str | None) -> None:
    await conn.execute(
        "update app.people set records_custodian_name = %s, records_custodian_email = %s"
        " where id = app.current_person_id()",
        (name, email),
    )
