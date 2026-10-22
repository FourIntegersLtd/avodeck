"""Slice O's business rules (architecture 10.1). SQL lives in `repo.py`."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import not_found
from app.core.contracts.org_policy import parse
from app.features.organisations import repo
from app.features.organisations.schemas import Organisation, Policy


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


async def rename(conn: AsyncConnection, org_id: uuid.UUID, name: str) -> Organisation:
    await repo.rename(conn, org_id, name)
    await audit.record(conn, action="organisation.renamed", target=f"organisation:{org_id}", organisation_id=org_id)
    return await organisation(conn, org_id)
