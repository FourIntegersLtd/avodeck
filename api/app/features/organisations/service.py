"""Slice O's business rules (architecture 10.1). SQL lives in `repo.py`."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import not_found
from app.core.auth.org_access import OrgAccess
from app.core.contracts.org_policy import parse
from app.core.errors import CONFLICT_SQLSTATE, ApiError
from app.features.organisations import repo
from app.features.organisations.schemas import Member, Organisation, Policy


def to_policy(raw: Any) -> Policy:
    policy = parse(raw)
    return Policy(required_template_id=policy.required_template_id, max_retention_years=policy.max_retention_years)


def to_organisation(row: dict[str, Any]) -> Organisation:
    return Organisation(
        id=row["id"], name=row["name"], kind=row["kind"], region=row["region"], role=row["role"],
        policy=to_policy(row["policy"]), created_at=row["created_at"],
    )  # fmt: skip


async def organisations(conn: AsyncConnection) -> list[Organisation]:
    return [to_organisation(r) for r in await repo.my_organisations(conn)]


async def organisation(conn: AsyncConnection, org_id: uuid.UUID) -> Organisation:
    row = await repo.my_organisation(conn, org_id)
    if row is None:
        raise not_found()
    return to_organisation(row)


async def create_team(conn: AsyncConnection, name: str, region: str) -> Organisation:
    org_id = await repo.create_team(conn, name, region)
    return await organisation(conn, org_id)


def refusal(exc: psycopg.Error) -> ApiError | None:
    """A database refusal from slice O's triggers, as the API error the web app shows."""
    found = REFUSALS.get(exc.diag.message_primary or "") if exc.sqlstate == CONFLICT_SQLSTATE else None
    return None if found is None else ApiError(found.code, found.message, found.status)


@asynccontextmanager
async def refusals() -> AsyncIterator[None]:
    try:
        yield
    except psycopg.Error as exc:
        mapped = refusal(exc)
        if mapped is None:
            raise
        raise mapped from None


LAST_OWNER = ApiError("last_owner", "Make someone else an owner first.", 409)
REFUSALS = {
    "last_owner": LAST_OWNER,
    "personal_membership": ApiError("personal_organisation", "Your own practice stays while your account is open.", 409),
}


def forbidden() -> ApiError:
    return ApiError("forbidden", "You cannot do that for this organisation.", 403)


async def members(conn: AsyncConnection, org_id: uuid.UUID) -> list[Member]:
    return [Member(**r) for r in await repo.members(conn, org_id)]


async def _target(conn: AsyncConnection, access: OrgAccess, membership_id: uuid.UUID, new_role: str | None) -> dict[str, Any]:
    target = await repo.current_membership(conn, access.org_id, membership_id)
    if target is None:
        raise not_found()
    if access.role != "owner" and (target["role"] == "owner" or new_role == "owner"):
        raise forbidden()
    return target


async def change_role(conn: AsyncConnection, access: OrgAccess, membership_id: uuid.UUID, role: str) -> Member:
    await _target(conn, access, membership_id, role)
    async with refusals():
        await repo.set_role(conn, membership_id, role)
    await audit.record(
        conn, action="membership.role_changed", target=f"membership:{membership_id}", organisation_id=access.org_id
    )
    [member] = [m for m in await members(conn, access.org_id) if m.membership_id == membership_id]
    return member


async def end_membership(conn: AsyncConnection, access: OrgAccess, membership_id: uuid.UUID) -> None:
    await _target(conn, access, membership_id, None)
    async with refusals():
        await repo.end_membership(conn, membership_id)
    await audit.record(conn, action="membership.ended", target=f"membership:{membership_id}", organisation_id=access.org_id)


async def rename(conn: AsyncConnection, org_id: uuid.UUID, name: str) -> Organisation:
    await repo.rename(conn, org_id, name)
    await audit.record(conn, action="organisation.renamed", target=f"organisation:{org_id}", organisation_id=org_id)
    return await organisation(conn, org_id)
