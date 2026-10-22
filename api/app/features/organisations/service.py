"""Slice O's business rules (architecture 10.1). SQL lives in `repo.py`."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import psycopg
from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import not_found
from app.core.auth.org_access import OrgAccess
from app.core.contracts.org_policy import parse
from app.core.email import Mailer, invite_mail
from app.core.errors import CONFLICT_SQLSTATE, ApiError, conflict
from app.core.notifications import send_mail
from app.features.organisations import repo
from app.features.organisations.schemas import Invite, InviteAccepted, Member, Organisation, Policy


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
    "invite_not_found": not_found(),
    "invite_expired": ApiError("invite_expired", "This invitation has expired. Ask for a new one.", 409),
    "invite_closed": ApiError("invite_closed", "This invitation has already been used or cancelled.", 409),
    "already_member": ApiError("already_member", "You are already a member of this organisation.", 409),
}
INVITE_TTL = timedelta(days=7)


def personal_organisation() -> ApiError:
    return ApiError("personal_organisation", "Invite people to a team, not to your own practice.", 409)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def invites(conn: AsyncConnection, org_id: uuid.UUID) -> list[Invite]:
    return [Invite(**r) for r in await repo.invites(conn, org_id)]


async def send_invite(
    conn: AsyncConnection, mailer: Mailer, access: OrgAccess, app_origin: str, email: str, role: str
) -> Invite:
    if access.kind != "team":
        raise personal_organisation()
    if access.role != "owner" and role == "owner":
        raise forbidden()
    token = secrets.token_urlsafe(32)
    invite_id = await repo.add_invite(conn, access.org_id, email, role, token_hash(token))
    sent = await repo.invite(conn, access.org_id, invite_id)
    assert sent is not None
    await audit.record(conn, action="invite.sent", target=f"invite:{invite_id}", organisation_id=access.org_id)
    mail = invite_mail(
        email, organisation_id=access.org_id, role=role, link=f"{app_origin}/invites/{token}",
        expires_on=sent["expires_at"].date(),
    )  # fmt: skip
    await send_mail(mailer, mail)
    return Invite(**sent)


async def cancel_invite(conn: AsyncConnection, org_id: uuid.UUID, invite_id: uuid.UUID) -> None:
    found = await repo.invite(conn, org_id, invite_id)
    if found is None:
        raise not_found()
    if found["status"] != "pending":
        raise conflict()
    await repo.cancel_invite(conn, invite_id)
    await audit.record(conn, action="invite.cancelled", target=f"invite:{invite_id}", organisation_id=org_id)


async def accept_invite(conn: AsyncConnection, token: str) -> InviteAccepted:
    async with refusals():
        accepted = await repo.accept_invite(conn, token_hash(token))
    if accepted is None:  # pragma: no cover - the function answers one row or refuses
        raise not_found()
    return InviteAccepted(**accepted)


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


async def leave(conn: AsyncConnection, org_id: uuid.UUID) -> None:
    """Ends my own membership; named-expert access to my named cases is kept (F31: it never came from it)."""
    mine = await repo.my_membership_id(conn, org_id)
    if mine is None:
        raise not_found()
    async with refusals():
        await repo.end_membership(conn, mine)
    await audit.record(conn, action="membership.left", target=f"membership:{mine}", organisation_id=org_id)


async def rename(conn: AsyncConnection, org_id: uuid.UUID, name: str) -> Organisation:
    await repo.rename(conn, org_id, name)
    await audit.record(conn, action="organisation.renamed", target=f"organisation:{org_id}", organisation_id=org_id)
    return await organisation(conn, org_id)
