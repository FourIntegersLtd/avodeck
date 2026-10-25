"""Support access to one case (architecture 10.1 "Support access"; S17, F38). SQL lives in `repo.py`.

The named expert gives Avodeck support at most 24 hours on one case (`support_access_grants`, migration
0030, which also checks the grant on every read in `app.case_access()`). One active grant per case: a new
one ends the earlier. Grants and revocations go to the case's audit trail, with ids and a code only.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import not_found
from app.core.email import Mailer, support_access_granted_mail
from app.core.errors import ApiError, conflict
from app.core.notifications import send_mail
from app.core.packs import MergedPack, PackError, PackLibrary
from app.features.organisations import repo
from app.features.organisations.schemas import (
    SupportCase,
    SupportCaseDetail,
    SupportCaseGrant,
    SupportGrant,
    SupportGrantCreate,
    SupportGrantList,
)


async def grants(conn: AsyncConnection, case_id: uuid.UUID) -> list[SupportGrant]:
    return [SupportGrant(**r) for r in await repo.grants(conn, case_id)]


async def grant_list(conn: AsyncConnection, case_id: uuid.UUID) -> SupportGrantList:
    every = await grants(conn, case_id)
    live = [g for g in every if g.active]
    return SupportGrantList(grants=every, active=live[-1] if live else None, ended=[g for g in every if not g.active])


async def give(
    conn: AsyncConnection, mailer: Mailer, case_id: uuid.UUID, body: SupportGrantCreate, expert_email: str
) -> SupportGrant:
    if body.granted_to is not None and not await repo.is_support_staff(conn, body.granted_to):
        raise ApiError("invalid", "Some details are not valid.", 422, fields=["granted_to"])
    await repo.lock_case(conn, case_id)
    await repo.end_active_grants(conn, case_id)
    grant_id = await repo.add_grant(conn, case_id, body.reason, body.granted_to)
    given = await repo.grant(conn, case_id, grant_id)
    assert given is not None
    await audit.record(conn, action="support_access.granted", target=f"grant:{grant_id}", case_id=case_id)
    mail = support_access_granted_mail(expert_email, case_id=case_id, grant_id=grant_id, expires_at=given["expires_at"])
    await send_mail(mailer, mail)
    return SupportGrant(**given)


LOOKED_UP = "support.case_looked_up"


def _query(q: str) -> tuple[uuid.UUID | None, str | None]:
    try:
        return uuid.UUID(q), None
    except ValueError:
        return None, (q if "@" in q else None)


async def console_search(conn: AsyncConnection, q: str) -> list[SupportCase]:
    case_id, email = _query(q.strip())
    if case_id is None and email is None:
        return []
    found = [SupportCase(**r) for r in await repo.support_cases(conn, case_id, email)]
    for case in found:  # every case a search shows is a look at it (F38): in its audit trail
        await audit.record(conn, action=LOOKED_UP, target=f"case:{case.case_id}", case_id=case.case_id)
    return found


def declaration_version(pack: MergedPack) -> str:
    """The fingerprint of the pack's declaration wording that the sign step compares (F34).

    The same sum as slice F's `report.checks.declaration_version`; slices may not import each other, so a
    test (test_organisations_support_diagnostics_routes.py) holds the two equal.
    """
    joined = "\n".join(f"{d['key']}@{d.get('version', '')}\n{d['text']}" for d in pack.declarations())
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


def _correction_pending(library: PackLibrary, packs: dict[str, str], acknowledged: str | None) -> bool:
    if acknowledged is None:
        return False
    try:
        pack = library.combine(packs["jurisdiction"], packs["case_type"], packs["specialty"])
    except (PackError, KeyError):
        return False
    return acknowledged != declaration_version(pack)


async def console_case(conn: AsyncConnection, library: PackLibrary, case_id: uuid.UUID) -> SupportCaseDetail:
    found = await repo.support_cases(conn, case_id, None)
    diagnostics = await repo.support_case_diagnostics(conn, case_id)
    if not found or diagnostics is None:
        raise not_found()
    rows = await repo.support_case_grants(conn, case_id)
    await audit.record(conn, action=LOOKED_UP, target=f"case:{case_id}", case_id=case_id)
    packs: dict[str, str] = diagnostics.pop("packs")
    report: dict[str, Any] = diagnostics["report"]
    acknowledged = report.pop("acknowledged_declaration_version")
    report["pack_correction_pending"] = _correction_pending(library, packs, acknowledged)
    return SupportCaseDetail(
        case=SupportCase(**found[0]),
        grants=[SupportCaseGrant(**{k: v for k, v in r.items() if k != "mine"}) for r in rows],
        content_access=any(r["mine"] for r in rows),
        **diagnostics,
    )


async def revoke(conn: AsyncConnection, case_id: uuid.UUID, grant_id: uuid.UUID) -> None:
    found = await repo.grant(conn, case_id, grant_id)
    if found is None:
        raise not_found()
    if not found["active"]:
        raise conflict()
    await repo.revoke_grant(conn, grant_id)
    await audit.record(conn, action="support_access.revoked", target=f"grant:{grant_id}", case_id=case_id)
