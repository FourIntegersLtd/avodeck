"""Helpers shared by slice G's routes and jobs: access, the case's packs, sealed text, the served version,
its paragraphs, page words and citation checks.

Every text slice G keeps (questions, answers, claims, directions, changes) is sealed with the case key
under its own purpose; nothing here logs or returns patient words in an error.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, Request
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.citations import Citation, quote_within
from app.core.contracts.document_words import word_purpose
from app.core.contracts.facts import citable_facts
from app.core.contracts.reading import PageReadLog
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import DbConn
from app.core.packs import END_OF_DAY, MergedPack, PackLibrary
from app.core.storage import SupabaseStorage, case_prefix

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Setup = Annotated[CaseAccess, Depends(require_case_access("case.edit_setup"))]
Upload = Annotated[CaseAccess, Depends(require_case_access("case.upload"))]
Write = Annotated[CaseAccess, Depends(require_case_access("opinion.write"))]
Sign = Annotated[CaseAccess, Depends(require_case_access("report.sign"))]

CODE = r"^[a-z][a-z0-9_]{0,39}$"

# Purposes each sealed text is kept under.
QUESTION = "written_question"
ANSWER = "written_question_answer"
CLAIM = "opposing_claim"
CLAIM_VALUE = "opposing_claim_value"
DIRECTION = "court_direction"
PARAGRAPH = "report_paragraph"
CHANGE_OLD = "report_change_old"
CHANGE_NEW = "report_change_new"
CHANGE_REASON = "report_change_reason"

# Report version types that are not the report itself.
NOT_MAIN = ("answers", "supplementary")


def not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


def invalid(*fields: str) -> ApiError:
    return ApiError("invalid", "Check the highlighted fields.", 422, fields=list(fields))


def conflict(code: str, message: str) -> ApiError:
    return ApiError(code, message, 409)


class Queued(BaseModel):
    job_id: uuid.UUID


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = request.app.state.crypto
    if crypto is None:
        raise ApiError("unavailable", "Saving is not ready.", 503)
    return crypto


def _storage(request: Request) -> SupabaseStorage:
    storage: SupabaseStorage | None = request.app.state.storage
    if storage is None:
        raise ApiError("unavailable", "Files are not ready.", 503)
    return storage


def _reads(request: Request) -> PageReadLog | None:
    reads: PageReadLog | None = getattr(request.app.state, "page_reads", None)
    return reads


Crypto = Annotated[CaseCrypto, Depends(_crypto)]
Storage = Annotated[SupabaseStorage, Depends(_storage)]
Reads = Annotated[PageReadLog | None, Depends(_reads)]


async def pack_for(conn: Any, library: PackLibrary, case_id: uuid.UUID) -> MergedPack:
    cur = await conn.execute("select * from app.after_serving_case_packs(%s)", (case_id,))
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    return library.combine(str(row[0]), str(row[1]), str(row[2]))


async def case_pack(request: Request, conn: DbConn, case_id: uuid.UUID) -> MergedPack:
    return await pack_for(conn, request.app.state.packs, case_id)


Pack = Annotated[MergedPack, Depends(case_pack)]


# --- sealed text ------------------------------------------------------------------------------------------


async def seal(conn: Any, crypto: CaseCrypto, case_id: uuid.UUID, text: str, purpose: str) -> bytes:
    return await crypto.encrypt(conn, case_id, text.encode(), purpose=purpose)


async def unseal(conn: Any, crypto: CaseCrypto, case_id: uuid.UUID, data: Any, purpose: str) -> str | None:
    if data is None:
        return None
    return (await crypto.decrypt(conn, case_id, bytes(data), purpose=purpose)).decode()


async def person_id(conn: Any) -> uuid.UUID | None:
    cur = await conn.execute("select app.current_person_id()")
    row = await cur.fetchone()
    return row[0] if row else None


def due_at(due_on: date, timezone: str) -> datetime:
    """The end of the court day in the court's timezone, in UTC."""
    return datetime.combine(due_on, END_OF_DAY, tzinfo=ZoneInfo(timezone)).astimezone(UTC)


def report_file(case_id: uuid.UUID, version_id: uuid.UUID, name: str) -> str:
    return f"{case_prefix(case_id)}/report/{version_id}/{name}"


# --- the served report ----------------------------------------------------------------------------------


async def served_version(conn: Any, case_id: uuid.UUID) -> uuid.UUID | None:
    """The served report itself (not answers or a supplementary report), latest first."""
    cur = await conn.execute(
        "select id from app.report_versions where case_id = %s and status = 'served' and type <> all(%s)"
        " order by version_no desc limit 1",
        (case_id, list(NOT_MAIN)),
    )
    row = await cur.fetchone()
    return row[0] if row else None


@dataclass(frozen=True)
class Paragraph:
    id: uuid.UUID
    para_no: int
    text: str
    fact_ids: list[uuid.UUID]


async def paragraphs(conn: Any, crypto: CaseCrypto, case_id: uuid.UUID, version_id: uuid.UUID) -> list[Paragraph]:
    cur = await conn.execute(
        "select paragraph_id, fact_id from app.citations where report_version_id = %s and case_id = %s"
        " order by paragraph_id, cite_no",
        (version_id, case_id),
    )
    cites: dict[uuid.UUID, list[uuid.UUID]] = {}
    for paragraph_id, fact_id in await cur.fetchall():
        cites.setdefault(paragraph_id, []).append(fact_id)
    cur = await conn.execute(
        "select id, para_no, text from app.report_paragraphs where report_version_id = %s and case_id = %s"
        " order by para_no",
        (version_id, case_id),
    )
    out = []
    for pid, para_no, text in await cur.fetchall():
        plain = await unseal(conn, crypto, case_id, text, PARAGRAPH) or ""
        out.append(Paragraph(pid, int(para_no), plain, cites.get(pid, [])))
    return out


# --- pages ----------------------------------------------------------------------------------------------


async def page_words(conn: Any, crypto: CaseCrypto, case_id: uuid.UUID, page_id: uuid.UUID) -> list[tuple[str, str]]:
    """(word id, word) of a numbered page, in reading order."""
    cur = await conn.execute(
        "select word_id, text from app.ocr_words where page_id = %s and case_id = %s order by n", (page_id, case_id)
    )
    words = []
    for wid, sealed in await cur.fetchall():
        text = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page_id, str(wid)))
        words.append((str(wid), text.decode()))
    return words


# --- citations ------------------------------------------------------------------------------------------


async def check_citations(conn: Any, case_id: uuid.UUID, reads: PageReadLog | None, refs: Sequence[Citation]) -> None:
    """Every citation names a citable fact on its own page, quoting words of its value (rule 2)."""
    if not refs:
        return
    facts = {f.id: f for f in await citable_facts(conn, case_id, reads)}
    for ref in refs:
        fact = facts.get(ref.fact_id)
        if fact is None or fact.page_id != ref.page_id or not quote_within(ref.quote_verbatim, fact.value):
            raise invalid("citation_refs")


def citations_json(refs: Sequence[Citation]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in refs]


# --- rows -----------------------------------------------------------------------------------------------


async def fetch_all(conn: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return list(await cur.fetchall())


async def fetch_one(conn: Any, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        found: dict[str, Any] | None = await cur.fetchone()
        return found


def pack_version(pack: MergedPack) -> str:
    return str(pack.versions["jurisdiction"])[:200]
