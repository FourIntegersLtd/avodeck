"""Screen 14: where a witness statement and the records differ (T-14c, slice E brief).

The named expert asks for one of the case's documents to be compared (202, `compare_witness_statement`).
Only a document with pages whose source type the case's packs mark as a statement can be compared (else
422 `not_a_statement`); only those pages' words are sent.
Each claim kept is an exact run of the statement's words, shown beside the facts it was compared with.
The expert confirms the AI's label, gives their own verdict (`edited`) or dismisses the claim (If-Match on
the claim's version); each decision is audited. The database keeps the allowed status changes (0501).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import psycopg
from fastapi import Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from app.core import audit
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.core.packs import MergedPack
from app.features.write.common import Crypto, PointFact, Queued, Write, case_pack, invalid, not_found, refused
from app.features.write.router import router

Verdict = Literal["supports", "contradicts", "not_addressed"]


def claim_purpose(document_id: uuid.UUID, claim_index: int) -> str:
    """The purpose a claim's words are sealed under, so they cannot be moved to another claim."""
    return f"witness_claim/{document_id}/{claim_index}"


async def statement_pages(
    conn: DbConn, case_id: uuid.UUID, document_id: uuid.UUID, pack: MergedPack
) -> list[uuid.UUID]:
    """The document's pages whose source type the packs mark as a statement."""
    cur = await conn.execute(
        "select id from app.pages where case_id = %s and document_id = %s and source_type = any(%s)"
        " order by bundle_page_no",
        (case_id, document_id, sorted(pack.statement_sources())),
    )
    return [r[0] for r in await cur.fetchall()]


class ClaimOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    claim_index: int
    text_verbatim: str
    word_ids: list[str]
    verdict: str
    expert_verdict: str | None
    status: str
    version: int
    facts: list[PointFact]


class ClaimChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "edited", "dismissed"]
    expert_verdict: Verdict | None = None


async def _claims(
    conn: DbConn, crypto: CaseCrypto, case_id: uuid.UUID, where: str, args: tuple[Any, ...]
) -> list[ClaimOut]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from app.witness_claims where case_id = %s and " + where + " order by document_id, claim_index",
            (case_id, *args),
        )
        rows = await cur.fetchall()
        await cur.execute(
            "select f.id, f.page_id, pg.bundle_page_no as page_no, f.kind, f.value, f.event_date from app.facts f"
            " join app.pages pg on pg.id = f.page_id where f.id = any(%s)",
            ([f for r in rows for f in r["fact_ids"]],),
        )
        facts = {r["id"]: PointFact(**r) for r in await cur.fetchall()}
    out = []
    for r in rows:
        sealed = bytes(r["text_verbatim"])
        text = await crypto.decrypt(conn, case_id, sealed, purpose=claim_purpose(r["document_id"], r["claim_index"]))
        keep = ("id", "document_id", "claim_index", "word_ids", "verdict", "expert_verdict", "status", "version")
        out.append(
            ClaimOut(
                **{k: r[k] for k in keep},
                text_verbatim=text.decode(),
                facts=[facts[f] for f in r["fact_ids"] if f in facts],
            )
        )
    return out


async def open_claims(conn: DbConn, crypto: CaseCrypto, case_id: uuid.UUID) -> list[ClaimOut]:
    """The case's witness claims the expert has not dismissed."""
    return await _claims(conn, crypto, case_id, "status <> 'dismissed'", ())


@router.post("/cases/{case_id}/witness-statements/{document_id}/compare", response_model=Queued, status_code=202)
async def compare(case_id: uuid.UUID, document_id: uuid.UUID, request: Request, access: Write, conn: DbConn) -> Queued:
    """Ask for a statement in the case to be set beside the records (T-14c); the expert decides each claim."""
    cur = await conn.execute("select 1 from app.documents where case_id = %s and id = %s", (case_id, document_id))
    if await cur.fetchone() is None:
        raise not_found()
    if not await statement_pages(conn, case_id, document_id, await case_pack(request, conn, case_id)):
        raise ApiError(
            "not_a_statement",
            "This document is not marked as a statement, so it cannot be set beside the records.",
            422,
        )
    job_id = await registry.enqueue(conn, case_id, "compare_witness_statement")
    await conn.execute(
        "insert into app.witness_comparisons (case_id, document_id, job_id) values (%s, %s, %s)",
        (case_id, document_id, job_id),
    )
    return Queued(job_id=job_id)


@router.patch("/cases/{case_id}/witness-claims/{claim_id}", response_model=ClaimOut)
async def decide_claim(
    case_id: uuid.UUID,
    claim_id: uuid.UUID,
    change: ClaimChange,
    if_match: IfMatch,
    response: Response,
    access: Write,
    conn: DbConn,
    crypto: Crypto,
) -> ClaimOut:
    """Confirm a claim's label, give the expert's own verdict, or dismiss the claim."""
    cur = await conn.execute(
        "select version from app.witness_claims where case_id = %s and id = %s for update", (case_id, claim_id)
    )
    found = await cur.fetchone()
    if found is None:
        raise not_found()
    if found[0] != if_match:
        raise stale()
    if (change.status == "edited") != (change.expert_verdict is not None):
        raise invalid("expert_verdict")
    try:
        async with conn.transaction():
            await conn.execute(
                "update app.witness_claims set status = %s, expert_verdict = %s where id = %s",
                (change.status, change.expert_verdict, claim_id),
            )
    except psycopg.Error as exc:
        raise refused(exc) from None
    await audit.record(
        conn, action=f"witness_claim.{change.status}", target=f"witness_claim:{claim_id}", case_id=case_id
    )
    [out] = await _claims(conn, crypto, case_id, "id = %s", (claim_id,))
    response.headers["ETag"] = etag(out.version)
    return out
