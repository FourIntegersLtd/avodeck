"""Legal agreement acceptance: terms, privacy policy and DPA (migration 0039).

The frontend gates on ``GET /v1/legal-agreements/status`` after sign-in: if any
document's current version has not been accepted, the person must accept before
using the app.  ``POST /v1/legal-agreements/accept`` records acceptance (append-only).
"""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel

from app.core.http import CurrentSession, DbConn, router

legal_router = router(prefix="/v1/legal-agreements", tags=["legal"])

CURRENT_VERSIONS: dict[str, str] = {
    "terms": "2026-11-01.1",
    "privacy": "2026-11-01.1",
    "dpa": "2026-11-01.1",
}


class DocumentStatus(BaseModel):
    document: str
    current_version: str
    accepted_version: str | None


class LegalStatus(BaseModel):
    accepted: bool
    documents: list[DocumentStatus]


class AcceptResult(BaseModel):
    recorded: int


@legal_router.get("/status", response_model=LegalStatus)
async def legal_status(session: CurrentSession, conn: DbConn) -> LegalStatus:
    """Which legal documents the signed-in person has accepted and which still need acceptance."""
    cur = await conn.execute(
        "select document, version from app.legal_agreements"
        " where person_id = app.current_person_id() and organisation_id is null"
        " order by accepted_at desc"
    )
    rows = await cur.fetchall()
    latest: dict[str, str] = {}
    for doc, ver in rows:
        if doc not in latest:
            latest[doc] = ver

    documents = [
        DocumentStatus(
            document=doc,
            current_version=ver,
            accepted_version=latest.get(doc),
        )
        for doc, ver in CURRENT_VERSIONS.items()
    ]
    return LegalStatus(
        accepted=all(d.accepted_version == d.current_version for d in documents),
        documents=documents,
    )


@legal_router.post("/accept", response_model=AcceptResult)
async def accept_legal(request: Request, session: CurrentSession, conn: DbConn) -> AcceptResult:
    """Record acceptance of every document whose current version the person has not yet accepted."""
    cur = await conn.execute(
        "select document, version from app.legal_agreements"
        " where person_id = app.current_person_id() and organisation_id is null"
        " order by accepted_at desc"
    )
    rows = await cur.fetchall()
    latest: dict[str, str] = {}
    for doc, ver in rows:
        if doc not in latest:
            latest[doc] = ver

    to_accept = [
        (doc, ver)
        for doc, ver in CURRENT_VERSIONS.items()
        if latest.get(doc) != ver
    ]
    if not to_accept:
        return AcceptResult(recorded=0)

    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or None
    ua = (request.headers.get("user-agent") or "")[:512] or None

    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into app.legal_agreements (person_id, document, version, ip_address, user_agent)"
            " values (app.current_person_id(), %s, %s, %s, %s)",
            [(doc, ver, ip, ua) for doc, ver in to_accept],
        )
    return AcceptResult(recorded=len(to_accept))
